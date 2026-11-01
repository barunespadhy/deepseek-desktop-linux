#!/usr/bin/env bash
# Provision a local rpm/rpmbuild toolchain (extracted from Debian packages)
# for machines that cannot install rpm natively. Used by build-rpm.py for
# building AND validating the .rpm without root. Native rpm distros skip this
# and use the system rpmbuild instead.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="$ROOT/build/rpm-tools"

command -v rpmbuild >/dev/null 2>&1 && { echo "native rpmbuild found"; exit 0; }
[ -x "$TOOLS/root/usr/bin/rpmbuild" ] && { echo "rpm-tools already provisioned"; exit 0; }

mkdir -p "$TOOLS" && cd "$TOOLS"
PACKAGES="rpm rpm-common librpm9t64 librpmio9t64 librpmbuild9t64 libpopt0 libzstd1 liblzma5 \
libbz2-1.0 libsqlite3-0 libgcrypt20 libgpg-error0 libcap2 liblua5.3-0 libselinux1 libmagic1t64"
apt-get download $PACKAGES >/dev/null 2>&1 || {
  echo "apt-get download failed; install rpmbuild natively or check the network" >&2
  exit 1
}
for archive in *.deb; do dpkg-deb -x "$archive" root/; done
chmod +x root/usr/bin/rpm* root/usr/lib/rpm/* 2>/dev/null || true

cat > "$TOOLS/my-rpmrc" <<EOF
macrofiles: $TOOLS/root/usr/lib/rpm/macros:$TOOLS/root/usr/lib/rpm/platform/%{_target}/macros:$TOOLS/root/usr/lib/rpm/macros.d/macros.*
EOF
cat > "$TOOLS/macros.override" <<EOF
%_topdir $TOOLS/rpbt
%_builddir %{_topdir}/BUILD
%_buildrootdir %{_topdir}/BUILDROOT
%_tmppath $TOOLS/tmp
%buildroot %{_buildrootdir}/%{name}-%{version}-%{release}.x86_64
%_fileattrsdir $TOOLS/root/usr/lib/rpm/fileattrs
%_rpmconfigdir $TOOLS/root/usr/lib/rpm
EOF
mkdir -p "$TOOLS/tmp" "$TOOLS/db"
echo "rpm-tools provisioned at $TOOLS"
