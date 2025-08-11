/* 
 * Generic PID controller with:
 * - Proportional on setpoint (setpoint weighting, beta)
 * - Derivative on measurement (noise friendly) with low-pass filter (tau)
 * - Conditional anti-windup (integrator clamping at limits)
 * - Output limits
 * - Manual/Auto mode with bumpless transfer
 * - Time-proportioning helper for on/off actuators (relay/SSR)
 * - Simple hysteresis helper
 *
 * All math in double; switch to float if you need.
 */

#include "pid.h"

/* Initialize with tunings and limits. */
static inline void PID_Init(PID *pid,
                            double Kp, double Ki, double Kd,
                            double Ts,
                            double out_min, double out_max)
{
    pid->Kp = Kp;
    pid->Ki = Ki;
    pid->Kd = Kd;
    pid->Ts = (Ts > 0.0) ? Ts : 0.001;
    pid->beta = 1.0;   /* default = full setpoint weighting */
    pid->tau  = 0.01;  /* mild derivative filter by default */
    pid->out_min = out_min;
    pid->out_max = out_max;

    pid->in_auto = true;
    pid->manual_out = 0.0;

    pid->integrator   = 0.0;
    pid->d_term       = 0.0;
    pid->prev_meas    = 0.0;
    pid->first_update = true;

    pid->d_alpha = pid->Ts / (pid->tau + pid->Ts);
}

/* Optionally change advanced options. Call anytime. */
static inline void PID_SetAdvanced(PID *pid, double beta, double tau)
{
    if (beta < 0.0) beta = 0.0;
    if (beta > 1.0) beta = 1.0;
    pid->beta = beta;

    if (tau < 0.0) tau = 0.0;
    pid->tau = tau;
    pid->d_alpha = pid->Ts / (pid->tau + pid->Ts);
}

/* Optionally change output limits. Keeps integrator sane. */
static inline void PID_SetOutputLimits(PID *pid, double out_min, double out_max)
{
    pid->out_min = out_min;
    pid->out_max = out_max;
    if (pid->out_min > pid->out_max) {
        /* swap if user passed reversed */
        double t = pid->out_min; pid->out_min = pid->out_max; pid->out_max = t;
    }
    if (pid->integrator > pid->out_max) pid->integrator = pid->out_max;
    if (pid->integrator < pid->out_min) pid->integrator = pid->out_min;
}

/* Switch modes with bumpless transfer. */
static inline void PID_SetMode(PID *pid, bool in_auto, double current_output, double current_measurement, double setpoint)
{
    if (pid->in_auto && !in_auto) {
        /* Going AUTO -> MANUAL: freeze current output */
        pid->manual_out = current_output;
    } else if (!pid->in_auto && in_auto) {
        /* MANUAL -> AUTO: bumpless transfer
           Back-calculate integrator so u = manual_out = P + I + D */
        double e = setpoint - current_measurement;
        double p = pid->Kp * (pid->beta * setpoint - current_measurement);

        /* derivative term from stored filtered value (already includes Kd) */
        double d = pid->d_term;

        /* target integrator to match manual output */
        pid->integrator = pid->manual_out - (p + d);

        /* clamp */
        if (pid->integrator > pid->out_max) pid->integrator = pid->out_max;
        if (pid->integrator < pid->out_min) pid->integrator = pid->out_min;

        pid->prev_meas = current_measurement;
        pid->first_update = false;
    }
    pid->in_auto = in_auto;
}

/* Reset internal state (e.g., after a big setpoint jump you don’t want memory). */
static inline void PID_Reset(PID *pid, double measurement, double output)
{
    pid->integrator = output;
    pid->d_term = 0.0;
    pid->prev_meas = measurement;
    pid->first_update = true;
}

/* One PID update step. Call at fixed period Ts. Returns constrained output. */
static inline double PID_Update(PID *pid, double setpoint, double measurement)
{
    if (!pid->in_auto) {
        /* Manual mode: track derivative state for smooth re-entry */
        double dm = (measurement - pid->prev_meas) / pid->Ts;
        double d_unf = -pid->Kd * dm;                    /* derivative on measurement */
        pid->d_term += pid->d_alpha * (d_unf - pid->d_term);
        pid->prev_meas = measurement;
        /* Return operator-chosen output */
        if (pid->manual_out > pid->out_max) pid->manual_out = pid->out_max;
        if (pid->manual_out < pid->out_min) pid->manual_out = pid->out_min;
        return pid->manual_out;
    }

    /* ----- P term (setpoint-weighted) ----- */
    double p = pid->Kp * (pid->beta * setpoint - measurement);

    /* ----- D term: derivative of measurement, low-pass filtered ----- */
    double d;
    if (pid->first_update) {
        pid->d_term = 0.0;
        pid->first_update = false;
        pid->prev_meas = measurement;
    }
    {
        double dm = (measurement - pid->prev_meas) / pid->Ts;
        double d_unf = -pid->Kd * dm;                    /* derivative on measurement */
        pid->d_term += pid->d_alpha * (d_unf - pid->d_term);
        d = pid->d_term;
        pid->prev_meas = measurement;
    }

    /* ----- I term: conditional anti-windup (clamping) ----- */
    double e = setpoint - measurement;
    double i_candidate = pid->integrator + pid->Ki * pid->Ts * e;

    /* Predict output with tentative I to decide windup */
    double u_pre = p + i_candidate + d;

    /* If saturating high and error drives further high, block integration.
       If saturating low and error drives further low, block integration. */
    if (!((u_pre > pid->out_max && e > 0.0) || (u_pre < pid->out_min && e < 0.0))) {
        pid->integrator = i_candidate;
    }
    /* Combine and clamp */
    double u = p + pid->integrator + d;

    if (u > pid->out_max) u = pid->out_max;
    if (u < pid->out_min) u = pid->out_min;

    return u;
}

/* ==================== TIME-PROPORTIONING HELPERS ==================== */
/* For on/off actuators (relay/SSR/heater) use time-proportioning:
 * - Choose a window, e.g., 2.0s.
 * - Drive ON for (duty * window) seconds each window.
 * Call TPWM_Update() every control tick with the normalized command in [0..1].
 */

static inline void TPWM_Init(TPWM *tpwm, double window_s)
{
    tpwm->window_s = (window_s > 0.01) ? window_s : 0.01;
    tpwm->t_in_win = 0.0;
}

/* dt = elapsed time since last call (seconds). 
 * duty_norm will be derived from PID output mapped into [0..1].
 * Returns: 1 = ON, 0 = OFF.
 */
static inline int TPWM_Update(TPWM *tpwm, double duty_norm, double dt)
{
    if (duty_norm < 0.0) duty_norm = 0.0;
    if (duty_norm > 1.0) duty_norm = 1.0;

    tpwm->t_in_win += dt;
    if (tpwm->t_in_win >= tpwm->window_s) {
        tpwm->t_in_win -= tpwm->window_s; /* start new window */
    }
    double on_time = duty_norm * tpwm->window_s;
    return (tpwm->t_in_win < on_time) ? 1 : 0;
}

/* ======================== HYSTERESIS HELPER ========================= */
/* Classic on/off with deadband, independent of PID (useful for simple thermostats). */
static inline int HysteresisSwitch(double pv, double setpoint, double deadband, int prev_state)
{
    /* prev_state: 0=OFF, 1=ON */
    if (deadband < 0.0) deadband = 0.0;
    if (prev_state) {
        /* ON -> stay ON until pv rises above setpoint + db/2 (for cooling) or falls below setpoint - db/2 (for heating).
           Choose convention: here we assume "heater" (turn ON when pv < setpoint - db/2). */
        if (pv >= setpoint + deadband*0.5) return 0; /* turn OFF */
        return 1;                                     /* stay ON */
    } else {
        if (pv <= setpoint - deadband*0.5) return 1; /* turn ON */
        return 0;                                     /* stay OFF */
    }
}


