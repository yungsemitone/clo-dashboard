"""Dump all manager names from the database so we can find remaining acronyms."""
import yaml
from pathlib import Path
from src.db import init_db, get_session
from src.models.schema import Deal

config_path = Path(__file__).parent / "config.yaml"
with open(config_path) as f:
    config = yaml.safe_load(f)
init_db(config)
session = get_session(config)

deals = session.query(Deal).all()
manager_counts = {}
for d in deals:
    manager_counts[d.manager] = manager_counts.get(d.manager, 0) + 1

print(f"Total managers: {len(manager_counts)}")
print(f"Total deals: {len(deals)}")
print()
for mgr, count in sorted(manager_counts.items()):
    print(f"  {count:>3}  {mgr}")
session.close()
