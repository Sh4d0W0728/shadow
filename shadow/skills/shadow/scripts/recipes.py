#!/usr/bin/env python3
"""Small keyword router. Reads only packaged recipe metadata; no network or AI calls."""
import argparse
import json
import re
import sys
from pathlib import Path


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="List, show or provisionally select shadow editing recipes.")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list")
    show = sub.add_parser("show")
    show.add_argument("id")
    select = sub.add_parser("select")
    select.add_argument("query")
    select.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    catalog = json.loads((Path(__file__).resolve().parents[1] / "assets" / "recipes.json").read_text(encoding="utf-8-sig"))
    recipes = catalog["recipes"]
    if args.action == "list":
        emit({"recipes": [{"id": r["id"], "name": r["name"], "query_tags": r["query_tags"]} for r in recipes]})
    elif args.action == "show":
        recipe = next((r for r in recipes if r["id"] == args.id), None)
        if recipe is None:
            emit({"error": "Unknown recipe ID", "id": args.id})
            return 2
        technique_ids = set(recipe["core_techniques"] + recipe.get("optional_techniques", []))
        emit({"recipe": recipe, "techniques": [t for t in catalog["technique_index"] if t["id"] in technique_ids]})
    else:
        if not 1 <= args.limit <= 12:
            parser.error("--limit must be between 1 and 12")
        query = re.sub(r"\$(?:shadow|video-edit)\b", "", args.query.casefold())
        candidates = []
        for recipe in recipes:
            matched = []
            for tag in recipe["query_tags"]:
                token = tag.casefold()
                # Latin tags use token boundaries: 'mv' must not match an arbitrary word.
                hit = bool(re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", query)) if re.fullmatch(r"[a-z0-9 -]+", token) else token in query
                if hit:
                    matched.append(tag)
            if matched:
                candidates.append({"id": recipe["id"], "name": recipe["name"], "score": len(matched), "matched_tags": matched, "priority_signal": recipe.get("priority_signal")})
        # Broad editor/software words cannot outrank an explicit content type.
        candidates.sort(key=lambda r: (r["id"] == "general-story", -r["score"], r["id"]))
        specific = [r for r in candidates if r["id"] != "general-story"]
        comparison = specific or candidates
        emit({"query": args.query, "selection_method": "keyword_candidates_specific_first", "requires_material_review": True, "ambiguous": not candidates or (len(comparison) > 1 and comparison[0]["score"] == comparison[1]["score"]), "candidates": candidates[:args.limit], "next_step": "Use the user's purpose and actual media to choose one primary recipe; output defaults are overridable starting points."})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as exc:
        emit({"error": str(exc)})
        raise SystemExit(2)
