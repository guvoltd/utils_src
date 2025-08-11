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

#ifndef PID_H
#define PID_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdbool.h>

/* ========================= PID CORE ========================= */

typedef struct {
    /* Tunings */
    double Kp;             /* Proportional gain                */
    double Ki;             /* Integral gain   (per second)     */
    double Kd;             /* Derivative gain (seconds)        */
    double Ts;             /* Sample time (seconds)            */
    double beta;           /* Setpoint weighting for P term [0..1] */
    double tau;            /* Derivative filter time constant (s)  */

    /* Limits */
    double out_min;        /* Minimum output */
    double out_max;        /* Maximum output */

    /* Mode */
    bool   in_auto;        /* true = Auto, false = Manual */
    double manual_out;     /* Used in MANUAL mode / bumpless transfer */

    /* Internal state */
    double integrator;     /* Integral accumulator */
    double d_term;         /* Filtered derivative term */
    double prev_meas;      /* y[k-1] */
    bool   first_update;   /* Handle first-cycle derivative */

    /* Precomputed factor for derivative low-pass */
    double d_alpha;        /* Ts / (tau + Ts) */
} PID;

/* Initialize with tunings and limits. */
static inline void PID_Init(PID *pid,
                            double Kp, double Ki, double Kd,
                            double Ts,
                            double out_min, double out_max);

/* Optionally change advanced options. Call anytime. */
static inline void PID_SetAdvanced(PID *pid, double beta, double tau);

/* Optionally change output limits. Keeps integrator sane. */
static inline void PID_SetOutputLimits(PID *pid, double out_min, double out_max);

/* Switch modes with bumpless transfer. */
static inline void PID_SetMode(PID *pid, bool in_auto, double current_output, double current_measurement, double setpoint);

/* Reset internal state (e.g., after a big setpoint jump you don’t want memory). */
static inline void PID_Reset(PID *pid, double measurement, double output);

/* One PID update step. Call at fixed period Ts. Returns constrained output. */
static inline double PID_Update(PID *pid, double setpoint, double measurement);

/* ==================== TIME-PROPORTIONING HELPERS ==================== */
/* For on/off actuators (relay/SSR/heater) use time-proportioning:
 * - Choose a window, e.g., 2.0s.
 * - Drive ON for (duty * window) seconds each window.
 * Call TPWM_Update() every control tick with the normalized command in [0..1].
 */

typedef struct {
    double window_s;   /* length of one window */
    double t_in_win;   /* time elapsed within current window */
} TPWM;

static inline void TPWM_Init(TPWM *tpwm, double window_s);

/* dt = elapsed time since last call (seconds). 
 * duty_norm will be derived from PID output mapped into [0..1].
 * Returns: 1 = ON, 0 = OFF.
 */
static inline int TPWM_Update(TPWM *tpwm, double duty_norm, double dt);

/* ======================== HYSTERESIS HELPER ========================= */
/* Classic on/off with deadband, independent of PID (useful for simple thermostats). */
static inline int HysteresisSwitch(double pv, double setpoint, double deadband, int prev_state);

#ifdef __cplusplus
}
#endif
#endif /* PID_H */

