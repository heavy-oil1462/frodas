{
  description = "frodas - autonomous solar-powered greenhouse controller (ESPHome + MQTT + VictoriaMetrics + Grafana)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    # Shared ESPHome tooling (validation gate, QEMU simulator, protocol
    # test, mock device, server stack). Carries its own nix-qemu-espressif
    # input for the .#sim shell. Update with: nix flake update esphome-skills
    esphome-skills.url = "github:heavy-oil1462/esphome-skills";
  };

  outputs = { self, nixpkgs, esphome-skills }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system:
        f system nixpkgs.legacyPackages.${system});
    in
    {
      # default: everything for the validation gate, protocol test and
      # server stack. sim: + Espressif QEMU for tools/test_sim.py.
      devShells = forAllSystems (system: pkgs:
        esphome-skills.lib.mkShells {
          inherit pkgs system;
          shellHook = ''
            echo "frodas devshell - see tools/ and .claude/skills/"
            echo "  python3 tools/validate.py       # full validation gate"
            echo "  python3 tools/test_protocol.py  # local broker + mock device test"
            echo "  python3 tools/stack.py --help   # server stack management"
            echo "  nix develop .#sim               # + QEMU for tools/test_sim.py"
          '';
        });
    };
}
