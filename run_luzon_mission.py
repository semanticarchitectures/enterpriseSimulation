"""Run the Luzon HADR mission simulation and save outputs."""

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
    config_path = Path(__file__).parent / "mission_luzon_hadr.yaml"
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)

    print(f"Loading mission configuration from: {config_path}")
    config = load(config_path)
    print(f"  Mission: {config.mission_name} ({config.mission_id})")
    print(f"  Aircraft: {config.aircraft.aircraft_type}")
    print(f"  Active modules: {config.active_modules}")
    print(f"  Route: {config.route_graph.origin} → {config.route_graph.destination}")
    print(f"  Total legs: {len(config.route_graph.edges)}")
    total_distance = sum(e.distance_nm for e in config.route_graph.edges)
    print(f"  Total distance: {total_distance:.0f} nm")
    if config.airdrop_parameters:
        total_cargo = sum(i.weight for i in config.airdrop_parameters.cargo_manifest)
        print(f"  Cargo: {len(config.airdrop_parameters.cargo_manifest)} pallets, {total_cargo:.0f} lbs")
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
        print("✓ Simulation completed successfully!")
    else:
        print(f"✗ Simulation FAILED: {result.error}")
        print(f"  Failed module: {result.failed_module}")
        return

    # Get results
    timeline = reporter.get_timeline()
    metrics = reporter.get_metrics()

    print()
    print("=" * 70)
    print("MISSION SIMULATION RESULTS")
    print("=" * 70)
    print()
    print(f"  Mission ID:                {config.mission_id}")
    print(f"  Mission Name:              {config.mission_name}")
    print(f"  Total sim duration:        {metrics.total_duration:.1f} time units")
    print(f"  Total costs incurred:      ${metrics.total_costs:,.2f}")
    print(f"  Messages sent:             {metrics.messages_sent}")
    print(f"  Messages delivered:        {metrics.messages_delivered}")
    print(f"  Messages failed:           {metrics.messages_failed}")
    print(f"  Mean delivery time:        {metrics.mean_delivery_time:.2f} time units")
    print(f"  Chain completion times:    {metrics.chain_completion_times}")
    print(f"  Total events captured:     {len(timeline)}")
    print()

    # Print timeline
    print("EVENT TIMELINE:")
    print("-" * 70)
    for entry in timeline:
        detail = ""
        if entry.event_type == "gate_completion":
            detail = f" [{entry.payload.get('gate_name', '')}]"
        elif entry.event_type == "fuel_consumed":
            detail = f" [{entry.payload.get('leg_id', '')} | {entry.payload.get('fuel_quantity', 0):.1f} units]"
        elif entry.event_type == "extraction_start":
            detail = f" [{entry.payload.get('drop_zone_id', '')}]"
        elif entry.event_type == "extraction_complete":
            items = entry.payload.get('cargo_manifest', [])
            detail = f" [{len(items)} pallets delivered]"
        elif entry.event_type == "chain_completion":
            detail = f" [elapsed: {entry.payload.get('elapsed_time', 0):.1f} units]"

        print(f"  t={entry.timestamp:8.1f} | {entry.source_module:15s} | {entry.event_type}{detail}")

    print()
    print("=" * 70)

    # Fuel summary
    fuel_events = [e for e in timeline if e.event_type == "fuel_consumed"]
    if fuel_events:
        total_fuel = sum(e.payload.get("fuel_quantity", 0) for e in fuel_events)
        remaining = config.aircraft.initial_fuel_weight - total_fuel
        pct_used = (total_fuel / config.aircraft.initial_fuel_weight) * 100
        print()
        print("FUEL SUMMARY:")
        print(f"  Initial fuel:    {config.aircraft.initial_fuel_weight:,.0f} lbs")
        print(f"  Total consumed:  {total_fuel:,.1f} lbs")
        print(f"  Remaining:       {remaining:,.1f} lbs")
        print(f"  Fuel used:       {pct_used:.1f}%")
        print(f"  Legs completed:  {len(fuel_events)}")
        print()
        print("  Per-leg breakdown:")
        for e in fuel_events:
            leg = e.payload.get("leg_id", "unknown")
            qty = e.payload.get("fuel_quantity", 0)
            print(f"    {leg:45s} {qty:8.1f} lbs")

    print()

    # Save outputs
    json_path = output_dir / "luzon_hadr_results.json"
    csv_path = output_dir / "luzon_hadr_results.csv"

    json_output = reporter.export("json")
    csv_output = reporter.export("csv")

    json_path.write_text(json_output, encoding="utf-8")
    csv_path.write_text(csv_output, encoding="utf-8")

    print(f"Outputs saved:")
    print(f"  JSON: {json_path}")
    print(f"  CSV:  {csv_path}")


if __name__ == "__main__":
    main()
