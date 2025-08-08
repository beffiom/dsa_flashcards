{ pkgs ? import <nixpkgs> {} }:

let
  stdenv = pkgs.stdenv;

  # Python environment with needed PyPI packages
  pythonEnv = pkgs.python310.withPackages (ps: with ps; [
    playwright
    tqdm
    beautifulsoup4
    requests
  ]);
in

pkgs.mkShell {
  buildInputs = [
    pkgs.gcc               # For libstdc++.so.6 and gcc runtime libs
    pythonEnv              # Python + packages like playwright
    pkgs.nix-ld            # Compatibility wrapper for running dynamically linked executables

    # Playwright browsers prebuilt for NixOS
    pkgs.playwright-driver.browsers

    # Common GTK/GNOME and graphical dependencies needed by playwright Chromium
    pkgs.glib
    pkgs.gobject-introspection
    pkgs.nss
    pkgs.nspr
    pkgs.dbus
    pkgs.atk
    pkgs.cairo
    pkgs.pango

    # X11 libraries (from xorg)
    pkgs.xorg.libX11
    pkgs.xorg.libXcomposite
    pkgs.xorg.libXdamage
    pkgs.xorg.libXext
    pkgs.xorg.libXfixes
    pkgs.xorg.libXrandr
    pkgs.xorg.libXrender
    pkgs.xorg.libxshmfence

    # Other graphical and audio dependencies
    pkgs.libgbm
    pkgs.libxkbcommon
    pkgs.alsa-lib
    pkgs.at-spi2-atk
    pkgs.cups
    pkgs.gnome-keyring
    pkgs.expat
    pkgs.udev
  ];

  shellHook = ''
    # Add gcc libs to LD_LIBRARY_PATH for native extensions like greenlet
    export LD_LIBRARY_PATH=${pkgs.stdenv.cc.cc.lib}/lib:$LD_LIBRARY_PATH

    # Playwright browsers path points to nixpkgs bundled browsers
    export PLAYWRIGHT_BROWSERS_PATH=${pkgs.playwright-driver.browsers}

    # Skip host validation to avoid runtime warnings (optional)
    export PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true

    # Use nix-ld to run Playwright's node driver with proper dynamic linker support
    export PATH=$(nix eval --raw nixpkgs#nix-ld)/bin:$PATH

    echo "Playwright environment initialized."
  '';
}
