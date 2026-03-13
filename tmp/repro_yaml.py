from pathlib import Path
import traceback
from src.analyzers.dag_config_parser import DBTSchemaParser, GenericYAMLPipelineParser

repo = Path(r"C:\Users\henokt\AppData\Local\Temp\cartographer_repro2")
parser = DBTSchemaParser()
generic = GenericYAMLPipelineParser()

failed = []
for f in list(repo.rglob("*.yml")) + list(repo.rglob("*.yaml")):
    try:
        parser.parse(f)
        generic.parse(f)
    except Exception as exc:  # noqa: BLE001
        failed.append((f, exc))
        print("FAILED", f)
        traceback.print_exc()

print("failures", len(failed))
