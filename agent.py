#!/usr/bin/env python3
"""
Travel Itinerary Agent
======================
Place your Word (.docx) and PDF (.pdf) travel documents in the `docs/` folder.
The agent indexes them and answers custom itinerary queries.

Usage:
    python agent.py --ingest          # Index all docs in ./docs/
    python agent.py --chat            # Interactive chat
    python agent.py --query "..."     # Single query
"""

import argparse
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

console = Console()

DOCS_DIR = Path(__file__).parent / "docs"
CHROMA_DIR = Path(__file__).parent / ".chroma_db"


# ── Document loaders ────────────────────────────────────────────────────────

def load_pdf(path: Path) -> str:
    import pdfplumber
    text_parts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text_parts.append(t)
    return "\n".join(text_parts)


def load_docx(path: Path) -> str:
    from docx import Document
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def load_documents(docs_dir: Path) -> list[dict]:
    """Return list of {source, content} dicts for every PDF/DOCX in docs_dir."""
    docs = []
    for f in sorted(docs_dir.iterdir()):
        if f.suffix.lower() == ".pdf":
            console.print(f"  [cyan]Loading PDF:[/cyan] {f.name}")
            content = load_pdf(f)
        elif f.suffix.lower() == ".docx":
            console.print(f"  [cyan]Loading DOCX:[/cyan] {f.name}")
            content = load_docx(f)
        else:
            continue
        if content.strip():
            docs.append({"source": f.name, "content": content})
    return docs


# ── Vector store ─────────────────────────────────────────────────────────────

def get_vectorstore(force_reingest: bool = False):
    from langchain_community.vectorstores import Chroma
    from langchain_openai import OpenAIEmbeddings
    from langchain.text_splitter import RecursiveCharacterTextSplitter
    from langchain.schema import Document

    embeddings = OpenAIEmbeddings()

    if CHROMA_DIR.exists() and not force_reingest:
        console.print("[green]Loading existing vector index…[/green]")
        return Chroma(persist_directory=str(CHROMA_DIR), embedding_function=embeddings)

    console.print(Panel("[bold]Ingesting travel documents…[/bold]", style="blue"))
    raw_docs = load_documents(DOCS_DIR)
    if not raw_docs:
        console.print("[red]No PDF or DOCX files found in ./docs/  — add files and re-run with --ingest[/red]")
        sys.exit(1)

    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
    lc_docs = []
    for d in raw_docs:
        chunks = splitter.create_documents(
            [d["content"]],
            metadatas=[{"source": d["source"]}] * 1,  # will be per-chunk
        )
        for chunk in chunks:
            chunk.metadata["source"] = d["source"]
        lc_docs.extend(chunks)

    console.print(f"  [green]Total chunks:[/green] {len(lc_docs)}")
    vs = Chroma.from_documents(lc_docs, embeddings, persist_directory=str(CHROMA_DIR))
    console.print("[green]Index saved.[/green]")
    return vs


# ── Agent / QA chain ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert travel planner with deep knowledge of destinations worldwide.
You have access to the user's personal travel itinerary documents (past trips, saved ideas, notes).
Use the retrieved context to craft highly personalised, practical, and inspiring travel itineraries.

When creating an itinerary:
- Reference specific details from the documents when relevant (hotels, restaurants, activities)
- Structure the response by Day (Day 1, Day 2, …)
- Include practical tips (best time to visit, local transport, approximate costs where known)
- Be concise but thorough
- If the user's documents don't cover the requested destination, say so and offer general advice
"""


def build_chain(vectorstore):
    from langchain_openai import ChatOpenAI
    from langchain.chains import ConversationalRetrievalChain
    from langchain.memory import ConversationBufferMemory

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)
    memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True, output_key="answer")
    retriever = vectorstore.as_retriever(search_kwargs={"k": 6})

    chain = ConversationalRetrievalChain.from_llm(
        llm=llm,
        retriever=retriever,
        memory=memory,
        return_source_documents=True,
        verbose=False,
    )
    return chain


def ask(chain, question: str) -> str:
    result = chain.invoke({"question": question})
    answer = result["answer"]
    sources = {d.metadata.get("source", "?") for d in result.get("source_documents", [])}
    if sources:
        answer += f"\n\n---\n*Sources: {', '.join(sorted(sources))}*"
    return answer


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Travel Itinerary Agent")
    parser.add_argument("--ingest", action="store_true", help="Re-index all documents in ./docs/")
    parser.add_argument("--chat", action="store_true", help="Start interactive chat")
    parser.add_argument("--query", type=str, help="Single query (non-interactive)")
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        console.print("[red]Error: OPENAI_API_KEY environment variable not set.[/red]")
        console.print("Export it:  export OPENAI_API_KEY='sk-...'")
        sys.exit(1)

    DOCS_DIR.mkdir(exist_ok=True)

    if args.ingest:
        get_vectorstore(force_reingest=True)
        return

    vs = get_vectorstore(force_reingest=False)
    chain = build_chain(vs)

    if args.query:
        answer = ask(chain, args.query)
        console.print(Markdown(answer))
        return

    # Interactive chat
    console.print(Panel(
        "[bold cyan]✈  Travel Itinerary Agent[/bold cyan]\n"
        "Ask anything about your trips or request a custom itinerary.\n"
        "Type [bold]exit[/bold] or [bold]quit[/bold] to leave.",
        style="blue"
    ))

    while True:
        try:
            user_input = Prompt.ask("\n[bold green]You[/bold green]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Bye![/yellow]")
            break

        if user_input.strip().lower() in ("exit", "quit", "q"):
            console.print("[yellow]Bye![/yellow]")
            break

        if not user_input.strip():
            continue

        with console.status("[bold blue]Thinking…[/bold blue]"):
            answer = ask(chain, user_input)

        console.print(Panel(Markdown(answer), title="[bold blue]Agent[/bold blue]", border_style="blue"))


if __name__ == "__main__":
    main()
