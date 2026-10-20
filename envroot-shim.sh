#!/bin/sh
# Minimal Apollo `envroot` shim for Builder Toolbox tools on hosts without /apollo.
#
# Toolbox Python tools use a shebang like:
#   #!/apollo/sbin/envroot $ENVROOT/python3.10/bin/python3.10
#
# The kernel invokes:  envroot "$ENVROOT/python3.10/bin/python3.10" <script> [args...]
# where the first arg is a literal template (with $ENVROOT unexpanded).
#
# This shim locates the tool's environment root by walking up from the script
# path to the nearest `.envroot` marker, expands $ENVROOT in the template, and
# execs the resolved interpreter.

cmd_template="$1"
shift

script="$1"   # next arg is the script being run (kept in "$@" for exec)

find_envroot() {
  dir="$1"
  [ -f "$dir" ] && dir=$(dirname "$dir")
  while [ -n "$dir" ] && [ "$dir" != "/" ]; do
    if [ -e "$dir/.envroot" ]; then
      printf '%s\n' "$dir"
      return 0
    fi
    dir=$(dirname "$dir")
  done
  return 1
}

ENVROOT=$(find_envroot "$script") || {
  echo "envroot-shim: could not locate .envroot for '$script'" >&2
  exit 1
}
export ENVROOT

# Expand only $ENVROOT in the interpreter template.
interpreter=$(printf '%s' "$cmd_template" | sed "s|\$ENVROOT|$ENVROOT|g")

exec "$interpreter" "$@"
