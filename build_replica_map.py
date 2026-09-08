import json

replica_map = {}

# Shared "Cloud" replicas: numbered Cloud<N> schemas, same underlying table structure
replica_map["ReplicaC1"] = {
    "type": "shared_cloud",
    "schemas": [f"Cloud{i}" for i in range(1, 10)],  # Cloud1-Cloud9
}
replica_map["ReplicaC2"] = {
    "type": "shared_cloud",
    "schemas": [f"Cloud{i}" for i in range(13, 26) if i != 24],  # Cloud13-Cloud25 excl. Cloud24
}
replica_map["ReplicaC3"] = {
    "type": "shared_cloud",
    "schemas": [f"Cloud{i}" for i in range(26, 36)],  # Cloud26-Cloud35
}
replica_map["ReplicaC37"] = {
    "type": "shared_cloud_foreign",
    "schemas": ["Cloud37"],
    "note": "Kept separate - foreign clients",
}

# Dedicated replicas: one schema per client (schema name = client/tenant name)
def load_tenant_list(path):
    with open(path) as f:
        lines = [l.strip() for l in f.read().splitlines()]
    skip = {"TABLE_SCHEMA", "mysql", "information_schema", "performance_schema", "sys", ""}
    return [l for l in lines if l not in skip]

replica_map["ReplicaE5"] = {
    "type": "dedicated",
    "schemas": load_tenant_list("/mnt/user-data/uploads/ReplicaE5.csv"),
}
replica_map["ReplicaE6"] = {
    "type": "dedicated",
    "schemas": load_tenant_list("/mnt/user-data/uploads/ReplicaE6.csv"),
}
replica_map["ReplicaE7"] = {
    "type": "dedicated",
    "schemas": load_tenant_list("/mnt/user-data/uploads/ReplicaE7.csv"),
}

# Pending clarification from user - placeholder so the app structure is ready
replica_map["ReplicaECloud1"] = {
    "type": "pending_clarification",
    "schemas": [],
    "note": "Range/details not yet confirmed by user",
}

with open("redash_query_builder/data/replica_map.json", "w") as f:
    json.dump(replica_map, f, indent=1)

for r, d in replica_map.items():
    print(r, d["type"], "->", len(d["schemas"]), "schemas")
