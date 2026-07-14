{
  description = "frodas - autonomous solar-powered greenhouse controller (ESPHome + MQTT + VictoriaMetrics + Grafana)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = with pkgs; [
            # firmware
            esphome
            # linting / validation
            yamllint
            docker-compose # `docker-compose config` parses without a daemon
            # local broker + clients for protocol tests and debugging
            mosquitto
            # tools/*.py
            (python3.withPackages (ps: with ps; [ paho-mqtt ]))
            jq
          ];
          shellHook = ''
            echo "frodas devshell - see tools/ and .claude/skills/"
            echo "  python3 tools/validate.py       # full validation gate"
            echo "  python3 tools/test_protocol.py  # local broker + mock device test"
            echo "  python3 tools/stack.py --help   # server stack management"
          '';
        };
      });
    };
}
