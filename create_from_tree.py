import os

def parse_tree(lines):
    stack = []
    result = []
    for line in lines:
        # Ignore empty lines and comments
        line = line.rstrip()
        if not line or line.startswith(';'):
            continue
        # Count indentation (spaces or ├──/└──)
        indent = 0
        while line.startswith('│   ') or line.startswith('    '):
            indent += 1
            line = line[4:]
        if line.startswith('├── ') or line.startswith('└── '):
            line = line[4:]
        # Remove annotations in parentheses
        name = line.split('(')[0].strip()
        # Remove trailing slashes for directories
        is_dir = name.endswith('/')
        name = name.rstrip('/')
        # Adjust stack to current indent
        while len(stack) > indent:
            stack.pop()
        path = os.path.join(*stack, name) if stack else name
        result.append((path, is_dir))
        if is_dir:
            stack.append(name)
    return result

def create_structure(tree_file, root_dir='.'):
    with open(tree_file, 'r') as f:
        lines = f.readlines()
    structure = parse_tree(lines)
    for path, is_dir in structure:
        full_path = os.path.join(root_dir, path)
        if is_dir:
            os.makedirs(full_path, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            open(full_path, 'a').close()

if __name__ == '__main__':
    # Usage: python create_from_tree.py treeStructure [target_dir]
    import sys
    tree_file = sys.argv[1] if len(sys.argv) > 1 else 'treeStructure'
    target_dir = sys.argv[2] if len(sys.argv) > 2 else '.'
    create_structure(tree_file, target_dir)