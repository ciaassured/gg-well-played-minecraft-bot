"""Guard the single-server and storage invariants without third-party YAML."""

from pathlib import Path

manifest = (Path(__file__).parents[1] / "kubernetes" / "yrush-farm.yaml").read_text(
    encoding="utf-8"
)

documents = [document.strip() for document in manifest.split("\n---\n")]
deployments = [document for document in documents if "kind: Deployment" in document]
assert len(deployments) == 1, "the farm must contain exactly one Deployment"
server = deployments[0]
assert "name: yrush-paper" in server
assert "replicas: 1" in server
assert "type: Recreate" in server
assert "mountPath: /data" in server
assert "ephemeral-storage: 20Gi" in server
assert "ephemeral-storage: 50Gi" in server
assert "medium: Memory" not in server
assert "persistentVolumeClaim:" not in server
assert 'yrush.gg/local-ssd: "true"' in server

paper_service = next(
    document
    for document in documents
    if "kind: Service" in document and "name: yrush-paper" in document
)
assert "publishNotReadyAddresses: true" in paper_service
assert "port: 25565" in paper_service

clients = next(document for document in documents if "kind: StatefulSet" in document)
assert "replicas: 4" in clients
assert "podManagementPolicy: Parallel" in clients
assert "value: yrush-paper:25565" in clients

claims = [
    document for document in documents if "kind: PersistentVolumeClaim" in document
]
assert len(claims) == 1
assert "name: yrush-artifacts" in claims[0]

run_metadata = next(document for document in documents if "kind: ConfigMap" in document)
assert "YRUSH_SERVER_POD_UID" in run_metadata
assert "YRUSH_SERVER_RESTART_COUNT" in run_metadata

jobs = [document for document in documents if "kind: Job" in document]
assert len(jobs) == 3
for stage in ("canary", "tuning-canary", "proof"):
    job = next(document for document in jobs if f"name: yrush-{stage}" in document)
    assert "suspend: true" in job
    assert "claimName: yrush-artifacts" in job
    assert "name: yrush-run-metadata" in job

print("Kubernetes single-server farm invariants passed")
