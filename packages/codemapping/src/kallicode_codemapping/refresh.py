import hashlib
import os
import subprocess

from .embeddings import embed_and_upsert
from .graph_loader import GraphLoader
from .ingest_repo import LANG_BY_EXT, SourceFile, sync_repo
from .llm_enrich import describe_symbols
from .parser_treesitter import parse_file


def changed_paths(repo: str, since_sha: str, head_sha: str) -> tuple[list[str], list[str]]:
    out = subprocess.run(
        ["git", "-C", repo, "diff", "--name-status", since_sha, head_sha],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    changed: list[str] = []
    deleted: list[str] = []
    for line in out.splitlines():
        status, _, path = line.partition("\t")
        if os.path.splitext(path)[1] not in LANG_BY_EXT:
            continue
        (deleted if status.startswith("D") else changed).append(path)
    return changed, deleted


def load_file(repo: str, path: str) -> SourceFile | None:
    full = os.path.join(repo, path)
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return None
    return SourceFile(
        path=path,
        language=LANG_BY_EXT[os.path.splitext(path)[1]],
        sha256=hashlib.sha256(content.encode()).hexdigest(),
        content=content,
    )


def run_refresh() -> None:
    repo, head = sync_repo()
    loader = GraphLoader()
    last = loader.get_last_sha()
    if last is None:
        raise SystemExit("Sin SHA previo: ejecuta build_graph.py --full")
    if last == head:
        print("Grafo al dia:", head)
        return

    changed, deleted = changed_paths(repo, last, head)
    print(f"refresh: {len(changed)} cambiados, {len(deleted)} eliminados")

    if deleted:
        loader.delete_file_subgraph(deleted)

    files = [f for p in changed if (f := load_file(repo, p))]
    symbols = [sym for f in files for sym in parse_file(f)]
    loader.load_files(files)
    loader.load_symbols(symbols)

    described = describe_symbols(symbols)
    by_id = {d["id"]: d for d in described}
    items = [
        {
            "id": int(sym.id, 16) % (2**63),
            "text": f"{sym.name} {sym.signature} {by_id.get(sym.id, {}).get('description_en', '')}",
            "payload": {
                "file": sym.file,
                "kind": sym.kind,
                "name": sym.name,
                "layer": "code",
            },
        }
        for sym in symbols
    ]
    embed_and_upsert("code", items)

    loader.set_last_sha(head)
    print("refresh completo ->", head)


if __name__ == "__main__":
    run_refresh()