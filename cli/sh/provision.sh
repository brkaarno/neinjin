#!/bin/sh
# shellcheck shell=dash

set -eu

##############################################################

err () { echo "ERROR: $1" >&2; }
die () { err "$1"; exit 1; }

check_cmd () {
  command -v "$1" >/dev/null 2>&1
  return $?
}

download () {
  if check_cmd curl
  then curl -sSfL "$1" -o "$2"

  elif check_cmd wget
  then wget "$1" -O "$2"

  else die "need curl or wget!"
  fi
}

##############################################################

REPOROOT=$(realpath .)

echo REPOROOT is $REPOROOT
[ -f $REPOROOT/cli/sh/provision.sh ] || die "please run this script from Tenjin's root directory";

LOCALDIR=$REPOROOT/_local

mkdir -p "$LOCALDIR"

echo "Downloading and installing uv to $LOCALDIR"
download "https://astral.sh/uv/install.sh" "$LOCALDIR/uv-installer.sh"
env UV_UNMANAGED_INSTALL="$LOCALDIR" INSTALLER_PRINT_QUIET=1 \
                      sh "$LOCALDIR"/uv-installer.sh

# Write out an initial configuration file
cat > "$LOCALDIR/uv.toml" <<EOF
# See also https://docs.astral.sh/uv/configuration/files/
package = false
# Ensure that Tenjin's uv cache directory is kept separated
cache-dir = "$LOCALDIR/uv_cache"
EOF

"$LOCALDIR/uv" --version

# Now that we can reliably run Python,
# continue provisioning steps in a nicer language than shell.
$REPOROOT/cli/10j provision

