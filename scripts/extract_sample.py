#!/usr/bin/env python3
"""Extract essay samples from TSV training data.

This script extracts individual essay samples from the training_set_8.tsv file
for use in baseline validation and testing.
"""

import csv
from pathlib import Path

import typer

app = typer.Typer(
    name="extract-sample",
    help="Extract essay samples from TSV training data.",
)


@app.command()
def main(
    essay_id: int = typer.Option(
        ...,
        "--essay-id",
        "-e",
        help="Essay ID to extract from training data.",
    ),
    source: Path = typer.Option(
        ...,
        "--source",
        "-s",
        help="Path to source TSV file (e.g., data/training_set_8.tsv).",
        exists=True,
    ),
    output: Path = typer.Option(
        ...,
        "--output",
        "-o",
        help="Path to output text file.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed extraction logs.",
    ),
) -> None:
    """Extract a specific essay from the training TSV file.

    The TSV file format (ASAP Set 8):
    - Column 0: essay_id
    - Column 2: essay (the text content)
    - Columns 3+: rater scores for domains and traits
    """
    # Validate output directory exists
    output_file = Path(output)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Read TSV file and find the essay
    source_path = Path(source)
    essay_text = None

    with open(source_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        headers = next(reader)  # Skip header row

        if verbose:
            typer.echo("=" * 60)
            typer.echo("Essay Sample Extractor")
            typer.echo("=" * 60)
            typer.echo(f"Essay ID: {essay_id}")
            typer.echo(f"Source: {source}")
            typer.echo(f"Output: {output}")
            typer.echo("=" * 60)

        # Search for the essay by ID
        for row in reader:
            if len(row) > 0 and row[0].isdigit():
                row_essay_id = int(row[0])
                if row_essay_id == essay_id:
                    if len(row) > 2:
                        essay_text = row[2]
                    break

    if essay_text is None:
        typer.echo(f"Error: Essay ID {essay_id} not found in {source}", err=True)
        raise typer.Exit(1)

    # Write essay text to output file
    output_file.write_text(essay_text, encoding="utf-8")

    if verbose:
        typer.echo(f"Essay text length: {len(essay_text)} characters")

    typer.echo(f"✓ Extracted essay {essay_id} to {output}")


if __name__ == "__main__":
    app()
