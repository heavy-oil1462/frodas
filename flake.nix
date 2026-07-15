{
  description = "frodas - autonomous solar-powered greenhouse controller (ESPHome + MQTT + VictoriaMetrics + Grafana)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    # Espressif's QEMU fork (esp32 machine + open_eth NIC) for the simulator
    # (docs/SIMULATION.md, tools/test_sim.py). Deliberately NOT following our
    # nixpkgs: keeping upstream's lock means the substituted/cached build is
    # reused instead of compiling QEMU from source.
    nix-qemu-espressif.url = "github:SFrijters/nix-qemu-espressif";
  };

  outputs = { self, nixpkgs, nix-qemu-espressif }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system:
        f system nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAllSystems (system: pkgs:
        let
          basePackages = with pkgs; [
            # firmware
            esphome
            # linting / validation
            yamllint
            docker-compose # `docker-compose config` parses without a daemon
            # local broker + clients for protocol tests and debugging
            mosquitto
            # tools/*.py + sim/*.py
            (python3.withPackages (ps: with ps; [ paho-mqtt ]))
            jq
          ];
        in
        {
          default = pkgs.mkShell {
            packages = basePackages;
            shellHook = ''
              echo "frodas devshell - see tools/ and .claude/skills/"
              echo "  python3 tools/validate.py       # full validation gate"
              echo "  python3 tools/test_protocol.py  # local broker + mock device test"
              echo "  python3 tools/stack.py --help   # server stack management"
              echo "  nix develop .#sim               # + QEMU for tools/test_sim.py"
            '';
          };
          # Everything above + Espressif QEMU for the real-firmware simulator
          # (tools/test_sim.py, docs/SIMULATION.md). Separate shell: QEMU may
          # build from source (~20 min) and most contributors never need it.
          sim = pkgs.mkShell {
            packages = basePackages
              ++ [ nix-qemu-espressif.packages.${system}.qemu-esp32 ];
          };
        });
    };
}
