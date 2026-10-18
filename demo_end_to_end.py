"""
Aether - End-to-End Wiring Demo
==================================
Proves Modules 1-5 integrate correctly:

  hardware_probe (2)  -> decides tier
        |
  llm_provider (3)    -> cloud/local model, tier-aware
        |
  system_interface(1) -> used BY plugins for OS calls
        |
  file_automation_plugin (5) -> registers tools via agent_loop's contract
        |
  agent_loop (4)      -> ReAct loop ties it all together

Run with:  python demo_end_to_end.py
(You need ANTHROPIC_API_KEY set, or a local Ollama running, for the LLM
call to actually succeed — the wiring itself works either way.)
"""

import os

from aether.hardware_probe import HardwareProbe
from aether.llm_provider import CloudProvider, LocalProvider, ManagedLLM
from aether.system_interface import get_system_interface
from aether.agent_loop import AgentLoop, PluginRegistry, AgentStep
from aether.plugins.file_automation_plugin import FileAutomationPlugin


def confirm(tool_name: str, description: str, args: dict) -> bool:
    print(f"\n[CONFIRM NEEDED] {tool_name}: {description}\n  args={args}")
    answer = input("Approve? [y/N]: ").strip().lower()
    return answer == "y"


def on_step(step: AgentStep) -> None:
    print(f"[{step.kind}] {step.content}")


def main() -> None:
    # 1. Detect hardware tier
    probe = HardwareProbe()
    probe.detect()
    print(f"Detected tier: {probe.tier.value}")
    print(f"Tier config: {probe.config}")

    # 2. Build LLM (cloud preferred, local fallback)
    cloud = CloudProvider(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    local = LocalProvider(tier_config=probe.config)
    llm = ManagedLLM(preferred=cloud, fallback=local)

    # 3. OS interface
    sysiface = get_system_interface()

    # 4. Register plugins
    registry = PluginRegistry()
    registry.register(FileAutomationPlugin(sysiface))

    # Adaptive engine hook: on Lite tier or system contention, drop heavy plugins.
    def on_throttle(active: bool) -> None:
        if active:
            disabled = registry.disable_heavy_plugins()
            print(f"[Aether] System under load — disabled heavy plugins: {disabled}")
        else:
            print("[Aether] System load normal again.")

    probe.start_monitoring(on_throttle)

    # 5. Run the agent loop on a sample request
    agent = AgentLoop(llm=llm, registry=registry, confirm_callback=confirm, on_step=on_step)

    try:
        result = agent.run("Search my home folder for PDF files and tell me how many you found.")
        print("\n=== FINAL ANSWER ===")
        print(result)
    finally:
        probe.stop_monitoring()


if __name__ == "__main__":
    main()
