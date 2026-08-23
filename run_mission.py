"""Run the enterprise simulation and save outputs."""

from pathlib import Path

from enterprise_sim import (
    EventBus,
    ModuleRegistry,
    SimulationEngine,
    Reporter,
    AuthorizationModule,
    FiscalModule,
    C2MessageModule,
    CrossDomainGatewayModule,
    AerodynamicsModule,
    AirdropModule,
)
from enterprise_sim.config.loader import load


def main():
    config_path = Path(__file__).parent / "mission_config.yaml"
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)

    print(f"Loading mission configuration from: {config_path}")
    config = load(config_path)
    print(f"  Mission: {config.mission_name} ({config.mission_id})")
    print(f"  Active modules: {config.active_modules}")
    print()

    # Set up infrastructure
    event_bus = EventBus()
    registry = ModuleRegistry()
    reporter = Reporter(event_bus)

    # Register all domain modules
    registry.register(AuthorizationModule())
    registry.register(FiscalModule())
    registry.register(C2MessageModule())
    registry.register(CrossDomainGatewayModule())
    registry.register(AerodynamicsModule())
    registry.register(AirdropModule())

    # Create and run engine
    engine = SimulationEngine(registry, event_bus)

    errors = engine.load_configuration(config)
    if errors:
        print(f"Configuration errors: {errors}")
        return

    print("Running simulation...")
    result = engine.run(config)
    print()

    if result.success:
        print("Simulation completed successfully!")
    else:
        print(f"Simulation FAILED: {result.error}")
        print(f"  Failed module: {result.failed_module}")
        return

    # Get results
    timeline = reporter.get_timeline()
    metrics = reporter.get_metrics()

    print()
    print("=" * 60)
    print("SIMULATION RESULTS")
    print("=" * 60)
    print()
    print(f"  Total simulation duration: {metrics.total_duration:.1f} time units")
    print(f"  Total costs incurred:      ${metrics.total_costs:,.2f}")
    print(f"  Messages sent:             {metrics.messages_sent}")
    print(f"  Messages delivered:        {metrics.messages_delivered}")
    print(f"  Messages failed:           {metrics.messages_failed}")
    print(f"  Mean delivery time:        {metrics.mean_delivery_time:.2f} time units")
    print(f"  Chain completion times:    {metrics.chain_completion_times}")
    print(f"  Total events captured:     {len(timeline)}")
    print()

    # Print timeline summary
    print("EVENT TIMELINE:")
    print("-" * 60)
    for entry in timeline:
        print(f"  t={entry.timestamp:8.1f} | {entry.source_module:15s} | {entry.event_type}")
    print()

    # Save outputs
    json_path = output_dir / "simulation_results.json"
    csv_path = output_dir / "simulation_results.csv"

    json_output = reporter.export("json")
    csv_output = reporter.export("csv")

    json_path.write_text(json_output, encoding="utf-8")
    csv_path.write_text(csv_output, encoding="utf-8")

    print(f"Outputs saved:")
    print(f"  JSON: {json_path}")
    print(f"  CSV:  {csv_path}")


if __name__ == "__main__":
    main()
