#!/bin/sh
# shellcheck shell=dash

set -eu

##############################################################

err () { echo "ERROR: $1" >&2; }
die () { err "$1"; exit 1; }

sez () {
  echo "TENJIN SEZ: $1"
}

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

[ -f $REPOROOT/cli/sh/provision.sh ] || die "please run this script from Tenjin's root directory";

LOCALDIR=$REPOROOT/_local

if [ ! -f "$LOCALDIR/uv" ]
then
  sez "First we'll grab 'uv' to run Python,"
  sez "  then we'll use it to run the rest of the"
  sez "  provisioning steps."
  echo ""
  sez "Everything will be installed to _local/"
  echo ""
  sez "Downloading and installing uv to $LOCALDIR"

  mkdir -p "$LOCALDIR"
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
fi

# Now that we can reliably run Python,
# continue provisioning steps in a nicer language than shell.
wanted="${1:-all}"
if [ "$wanted" = "uv" ]; then
    # CI will provision pieces piecemeal in parallel jobs.
    sez 'Please run `10j provision` to continue setup.'
else
    $REPOROOT/cli/10j provision "$wanted"
fi


if ! command -v 10j >/dev/null 2>&1
then
  sez "Last thing, adding  $REPOROOT/cli  to your PATH"
  sez "             will let you run 10j from anywhere."
fi
sez "Cheers!"