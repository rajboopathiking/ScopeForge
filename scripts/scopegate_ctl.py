#!/usr/bin/env python3
"""ScopeGate Control Utility - Manage execution mode and authorized scopes."""
import argparse
import json
import sys
from pathlib import Path

# Config file location
CONFIG_DIR = Path(".scopeforge")


def get_config_file() -> Path:
    """Return existing config file path or default."""
    if (CONFIG_DIR / "scopegate.json").exists():
        return CONFIG_DIR / "scopegate.json"
    if (CONFIG_DIR / "scopegate_config.json").exists():
        return CONFIG_DIR / "scopegate_config.json"
    return CONFIG_DIR / "scopegate.json"


def load_config():
    """Load ScopeGate configuration from disk."""
    config_file = get_config_file()
    if not config_file.exists():
        return {"mode": "plan", "authorized_scopes": []}
    with open(config_file, "r") as f:
        data = json.load(f)
    mode = data.get("mode") or data.get("execution_mode", "plan")
    scopes = data.get("authorized_scopes") or data.get("authorized_targets", [])
    return {"mode": mode, "authorized_scopes": list(scopes)}


def save_config(config):
    """Persist ScopeGate configuration to disk."""
    CONFIG_DIR.mkdir(exist_ok=True)
    config_file = get_config_file()
    existing = {}
    if config_file.exists():
        try:
            with open(config_file, "r") as f:
                existing = json.load(f)
        except Exception:
            existing = {}
    existing["mode"] = config["mode"]
    existing["authorized_scopes"] = config["authorized_scopes"]
    if "execution_mode" in existing or config_file.name == "scopegate_config.json":
        existing["execution_mode"] = config["mode"]
        existing["authorized_targets"] = config["authorized_scopes"]
    with open(config_file, "w") as f:
        json.dump(existing, f, indent=2)


def set_mode(mode: str):
    """Set the ScopeGate execution mode."""
    valid_modes = ["plan", "artifacts", "live"]
    if mode not in valid_modes:
        print(f"❌ Invalid mode '{mode}'. Must be one of: {', '.join(valid_modes)}")
        sys.exit(1)

    config = load_config()
    config["mode"] = mode
    save_config(config)
    print(f"✅ ScopeGate mode set to: {mode.upper()}")
    print(f"   Config saved to: {get_config_file()}")


def add_scope(target: str):
    """Add an authorized target to the allowlist."""
    config = load_config()
    scopes = config.get("authorized_scopes", [])
    
    if target in scopes:
        print(f"ℹ️  Target '{target}' is already in authorized scopes")
    else:
        scopes.append(target)
        config["authorized_scopes"] = scopes
        save_config(config)
        print(f"✅ Added '{target}' to authorized scopes")
        print(f"   Total scopes: {len(scopes)}")


def remove_scope(target: str):
    """Remove a target from the authorized allowlist."""
    config = load_config()
    scopes = config.get("authorized_scopes", [])
    
    if target not in scopes:
        print(f"⚠️  Target '{target}' not found in authorized scopes")
    else:
        scopes.remove(target)
        config["authorized_scopes"] = scopes
        save_config(config)
        print(f"✅ Removed '{target}' from authorized scopes")
        print(f"   Remaining scopes: {len(scopes)}")


def list_config():
    """Display current ScopeGate configuration."""
    config = load_config()
    mode = config.get("mode", "plan")
    scopes = config.get("authorized_scopes", [])
    
    print("\n🔒 ScopeGate Configuration")
    print("─" * 50)
    print(f"Mode: {mode.upper()}")
    print(f"Authorized Scopes: {len(scopes)}")
    if scopes:
        for i, scope in enumerate(scopes, 1):
            print(f"  {i}. {scope}")
    else:
        print("  (none)")
    print(f"\nConfig file: {get_config_file()}")
    print("─" * 50 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="ScopeGate Control - Manage execution mode and authorized scopes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  # Set mode to live execution
  python scripts/scopegate_ctl.py set-mode live

  # Add authorized target
  python scripts/scopegate_ctl.py add-scope localhost
  python scripts/scopegate_ctl.py add-scope https://authorized.example.com

  # Show current config
  python scripts/scopegate_ctl.py status
        """,
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # set-mode command
    mode_parser = subparsers.add_parser("set-mode", help="Set ScopeGate execution mode")
    mode_parser.add_argument(
        "mode",
        choices=["plan", "artifacts", "live"],
        help="Execution mode (plan=dry-run, artifacts=file ops only, live=full network)",
    )
    
    # add-scope command
    add_parser = subparsers.add_parser("add-scope", help="Add authorized target")
    add_parser.add_argument("target", help="Target domain or IP to authorize")
    
    # remove-scope command
    rm_parser = subparsers.add_parser("remove-scope", help="Remove authorized target")
    rm_parser.add_argument("target", help="Target to remove from allowlist")
    
    # status command
    subparsers.add_parser("status", help="Show current ScopeGate configuration")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    if args.command == "set-mode":
        set_mode(args.mode)
    elif args.command == "add-scope":
        add_scope(args.target)
    elif args.command == "remove-scope":
        remove_scope(args.target)
    elif args.command == "status":
        list_config()


if __name__ == "__main__":
    main()
