# data_analyst.py — Universal Business Data Analyst Engine
# Supports CSV, Excel, PDF, and text file ingestion with
# PandasQueryEngine for tabular data and ChromaDB RAG for documents.

import os
import json
import uuid
import hashlib
import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

import pandas as pd
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DOCS_DIR = os.path.join(DATA_DIR, "analyst_docs")
CHARTS_DIR = os.path.join(DATA_DIR, "analyst_charts")
INDEX_FILE = os.path.join(DATA_DIR, "analyst_index.json")
CACHE_FILE = os.path.join(DATA_DIR, "analyst_cache.json")

os.makedirs(DOCS_DIR, exist_ok=True)
os.makedirs(CHARTS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# ─── FOLLOW-UP QUERY SESSION MEMORY ──────────────────────────────────────────
# Per-doc session: stores last N queries + code + results for context
_query_sessions = {}  # {doc_id: {"history": [...], "last_access": timestamp}}
SESSION_MAX_HISTORY = 5
SESSION_TIMEOUT_SEC = 1800  # 30 minutes


def _get_session(doc_id: str) -> list:
    """Get or create a query session for a document."""
    _cleanup_expired_sessions()
    if doc_id not in _query_sessions:
        _query_sessions[doc_id] = {"history": [], "last_access": time.time()}
    _query_sessions[doc_id]["last_access"] = time.time()
    return _query_sessions[doc_id]["history"]


def _add_to_session(doc_id: str, question: str, code: str, result_summary: str) -> None:
    """Append a query to the session history."""
    history = _get_session(doc_id)
    history.append({
        "question": question,
        "code": code,
        "result": result_summary[:300],  # Keep summaries compact
    })
    # Keep only the last N entries
    if len(history) > SESSION_MAX_HISTORY:
        _query_sessions[doc_id]["history"] = history[-SESSION_MAX_HISTORY:]


def clear_session(doc_id: str) -> bool:
    """Clear the query session for a document."""
    if doc_id in _query_sessions:
        del _query_sessions[doc_id]
        return True
    return False


def get_session_info(doc_id: str) -> dict:
    """Get session info (count of previous queries)."""
    if doc_id in _query_sessions:
        return {"count": len(_query_sessions[doc_id]["history"])}
    return {"count": 0}


def _cleanup_expired_sessions() -> None:
    """Remove sessions older than TIMEOUT."""
    now = time.time()
    expired = [k for k, v in _query_sessions.items() if now - v["last_access"] > SESSION_TIMEOUT_SEC]
    for k in expired:
        del _query_sessions[k]

# ─── LLM PROVIDER CHAIN ─────────────────────────────────────────────────────

def _query_ollama(prompt: str, system: str = "", model: str = "gemma2:9b") -> Optional[str]:
    """Local Ollama — unlimited, free, private."""
    try:
        import ollama
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = ollama.chat(model=model, messages=messages, options={
            "temperature": 0.1,
            "num_predict": 512,
        })
        return response["message"]["content"]
    except Exception as e:
        logger.warning(f"Ollama failed: {e}")
        return None


def _query_groq(prompt: str, system: str = "") -> Optional[str]:
    """Groq API — 14,400 req/day free tier."""
    try:
        from config import Config
        api_key = getattr(Config, "GROQ_API_KEY", None) or os.getenv("GROQ_API_KEY")
        if not api_key:
            return None
        import requests
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json={"model": "llama-3.3-70b-versatile", "messages": messages, "temperature": 0.1, "max_tokens": 512},
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        logger.warning(f"Groq API error: {resp.status_code}")
        return None
    except Exception as e:
        logger.warning(f"Groq failed: {e}")
        return None


def _query_gemini(prompt: str, system: str = "") -> Optional[str]:
    """Google Gemini Flash — 1,500 req/day free tier."""
    try:
        from config import Config
        api_key = getattr(Config, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY")
        if not api_key:
            return None
        import requests
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": full_prompt}]}],
                  "generationConfig": {"temperature": 0.1, "maxOutputTokens": 512}},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
        logger.warning(f"Gemini API error: {resp.status_code}")
        return None
    except Exception as e:
        logger.warning(f"Gemini failed: {e}")
        return None


def query_llm(prompt: str, system: str = "") -> str:
    """Multi-provider LLM chain: Ollama → Groq → Gemini → fallback."""
    # Try each provider in order
    for provider in [_query_ollama, _query_groq, _query_gemini]:
        result = provider(prompt, system)
        if result:
            return result
    return "⚠️ Could not generate a response. All LLM providers are unavailable."


# ─── DOCUMENT INDEX MANAGEMENT ───────────────────────────────────────────────

def _load_index() -> list:
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, "r") as f:
            return json.load(f)
    return []


def _save_index(index: list) -> None:
    with open(INDEX_FILE, "w") as f:
        json.dump(index, f, indent=2, default=str)


def _load_cache() -> dict:
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_cache(cache: dict) -> None:
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def list_documents() -> list:
    """Return all ingested documents with metadata."""
    return _load_index()


def delete_document(doc_id: str) -> bool:
    """Remove a document from the index and delete its file."""
    index = _load_index()
    doc = next((d for d in index if d["id"] == doc_id), None)
    if not doc:
        return False

    # Remove stored file
    stored_path = os.path.join(DOCS_DIR, doc.get("stored_filename", ""))
    if os.path.exists(stored_path):
        os.remove(stored_path)

    # Remove ChromaDB collection if it's a text doc
    if doc["type"] in ("pdf", "txt"):
        try:
            import chromadb
            client = chromadb.PersistentClient(path=os.path.join(BASE_DIR, "chroma_db"))
            collection_name = f"analyst_{doc_id[:8]}"
            try:
                client.delete_collection(collection_name)
            except Exception:
                pass
        except Exception:
            pass

    # Remove from index
    index = [d for d in index if d["id"] != doc_id]
    _save_index(index)

    # Clear related cache entries
    cache = _load_cache()
    cache = {k: v for k, v in cache.items() if doc_id not in k}
    _save_cache(cache)

    return True


# ─── FILE INGESTION ──────────────────────────────────────────────────────────

def ingest_file(file_path: str, original_filename: str = "") -> dict:
    """
    Ingest a CSV, Excel, PDF, or text file. Returns a summary card dict:
    {id, filename, type, summary, rows/pages, columns, upload_time}
    """
    if not original_filename:
        original_filename = os.path.basename(file_path)

    ext = os.path.splitext(original_filename)[1].lower()
    doc_id = str(uuid.uuid4())[:12]

    # Copy file to our managed directory
    stored_filename = f"{doc_id}_{original_filename}"
    stored_path = os.path.join(DOCS_DIR, stored_filename)

    import shutil
    shutil.copy2(file_path, stored_path)

    if ext in (".csv", ".xlsx", ".xls"):
        return _ingest_tabular(stored_path, original_filename, doc_id, ext)
    elif ext == ".pdf":
        return _ingest_pdf(stored_path, original_filename, doc_id)
    elif ext in (".txt", ".text", ".md"):
        return _ingest_text(stored_path, original_filename, doc_id)
    else:
        return {"error": f"Unsupported file type: {ext}. Supported: CSV, Excel, PDF, TXT"}


def _ingest_tabular(file_path: str, original_name: str, doc_id: str, ext: str) -> dict:
    """Ingest CSV or Excel into pandas DataFrame and generate summary."""
    try:
        if ext == ".csv":
            df = pd.read_csv(file_path)
        else:
            df = pd.read_excel(file_path)

        rows, cols = df.shape
        columns = list(df.columns)
        dtypes = {col: str(df[col].dtype) for col in columns}

        # Generate column summary
        col_summaries = []
        for col in columns[:15]:  # Show max 15 columns
            dtype = dtypes[col]
            if "float" in dtype or "int" in dtype:
                col_summaries.append(f"  • {col} ({dtype}) — min: {df[col].min()}, max: {df[col].max()}")
            else:
                nunique = df[col].nunique()
                col_summaries.append(f"  • {col} ({dtype}) — {nunique} unique values")

        summary = f"📊 **{original_name}** loaded!\n"
        summary += f"📏 {rows} rows × {cols} columns\n"
        summary += "📋 Columns:\n" + "\n".join(col_summaries)

        if len(columns) > 15:
            summary += f"\n  ...and {len(columns) - 15} more columns"

        doc_entry = {
            "id": doc_id,
            "filename": original_name,
            "stored_filename": os.path.basename(file_path),
            "type": "csv" if ext == ".csv" else "excel",
            "rows": rows,
            "columns": columns,
            "dtypes": dtypes,
            "summary": summary,
            "upload_time": datetime.now().isoformat(),
        }

        index = _load_index()
        index.append(doc_entry)
        _save_index(index)

        return doc_entry

    except Exception as e:
        return {"error": f"Failed to load {original_name}: {str(e)}"}


def _ingest_pdf(file_path: str, original_name: str, doc_id: str) -> dict:
    """Extract text from PDF, chunk, and store in ChromaDB."""
    try:
        import pdfplumber

        all_text = []
        page_count = 0

        with pdfplumber.open(file_path) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    all_text.append(text.strip())

        if not all_text:
            return {"error": f"Could not extract text from {original_name}. The PDF may be scanned/image-based."}

        full_text = "\n\n".join(all_text)

        # Chunk the text (~500 tokens per chunk with 50-token overlap)
        chunks = _chunk_text(full_text, chunk_size=500, overlap=50)

        # Store in ChromaDB
        _store_chunks_in_chromadb(doc_id, chunks, original_name)

        summary = f"📄 **{original_name}** loaded!\n"
        summary += f"📏 {page_count} pages, {len(chunks)} text chunks indexed\n"
        summary += f"📝 Preview: {full_text[:200]}..."

        doc_entry = {
            "id": doc_id,
            "filename": original_name,
            "stored_filename": os.path.basename(file_path),
            "type": "pdf",
            "pages": page_count,
            "chunks": len(chunks),
            "summary": summary,
            "upload_time": datetime.now().isoformat(),
        }

        index = _load_index()
        index.append(doc_entry)
        _save_index(index)

        return doc_entry

    except Exception as e:
        return {"error": f"Failed to load {original_name}: {str(e)}"}


def _ingest_text(file_path: str, original_name: str, doc_id: str) -> dict:
    """Ingest plain text file into ChromaDB."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            full_text = f.read()

        if not full_text.strip():
            return {"error": f"{original_name} is empty."}

        chunks = _chunk_text(full_text, chunk_size=500, overlap=50)
        _store_chunks_in_chromadb(doc_id, chunks, original_name)

        line_count = full_text.count("\n") + 1

        summary = f"📝 **{original_name}** loaded!\n"
        summary += f"📏 {line_count} lines, {len(chunks)} text chunks indexed\n"
        summary += f"Preview: {full_text[:200]}..."

        doc_entry = {
            "id": doc_id,
            "filename": original_name,
            "stored_filename": os.path.basename(file_path),
            "type": "txt",
            "lines": line_count,
            "chunks": len(chunks),
            "summary": summary,
            "upload_time": datetime.now().isoformat(),
        }

        index = _load_index()
        index.append(doc_entry)
        _save_index(index)

        return doc_entry

    except Exception as e:
        return {"error": f"Failed to load {original_name}: {str(e)}"}


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list:
    """Split text into chunks of ~chunk_size words with overlap."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + chunk_size])
        chunks.append(chunk)
        i += chunk_size - overlap
    return chunks


def _store_chunks_in_chromadb(doc_id: str, chunks: list, source_name: str) -> None:
    """Store text chunks in a per-document ChromaDB collection."""
    try:
        import chromadb
        client = chromadb.PersistentClient(path=os.path.join(BASE_DIR, "chroma_db"))
        collection_name = f"analyst_{doc_id[:8]}"
        collection = client.get_or_create_collection(name=collection_name)

        ids = [f"{doc_id}_chunk_{i}" for i in range(len(chunks))]
        metadatas = [{"source": source_name, "chunk_index": i} for i in range(len(chunks))]

        # Batch insert (ChromaDB handles embedding via its default model)
        batch_size = 50
        for start in range(0, len(chunks), batch_size):
            end = min(start + batch_size, len(chunks))
            collection.add(
                documents=chunks[start:end],
                ids=ids[start:end],
                metadatas=metadatas[start:end],
            )

        logger.info(f"✅ Stored {len(chunks)} chunks in ChromaDB collection '{collection_name}'")
    except Exception as e:
        logger.error(f"ChromaDB storage error: {e}")
        raise


# ─── QUERY ENGINE ────────────────────────────────────────────────────────────

def query_data(question: str, doc_id: Optional[str] = None) -> dict:
    """
    Answer a natural-language question about uploaded data.
    Routes to PandasQueryEngine for tabular data, ChromaDB RAG for text.

    Returns: {answer, source, confidence}
    """
    # Check cache first
    cache_key = hashlib.sha256(f"{question}:{doc_id or 'all'}".encode()).hexdigest()[:16]
    cache = _load_cache()
    if cache_key in cache:
        cached = cache[cache_key]
        cached["from_cache"] = True
        return cached

    index = _load_index()
    if not index:
        return {"answer": "⚠️ Koi document upload nahi hua hai. Pehle ek CSV, PDF, ya text file upload karo.", "source": "", "confidence": 0}

    # Find the target document
    if doc_id:
        doc = next((d for d in index if d["id"] == doc_id), None)
        if not doc:
            return {"answer": f"⚠️ Document '{doc_id}' nahi mila.", "source": "", "confidence": 0}
        docs_to_query = [doc]
    else:
        # Query the most recently uploaded document
        docs_to_query = [index[-1]]

    doc = docs_to_query[0]

    if doc["type"] in ("csv", "excel"):
        # For descriptive questions on tabular data, generate an overview
        if _is_descriptive_question(question):
            result = _describe_tabular(question, doc)
        else:
            result = _query_tabular(question, doc)
    elif doc["type"] in ("pdf", "txt"):
        result = _query_text_rag(question, doc)
    else:
        result = {"answer": "⚠️ Unsupported document type.", "source": doc["filename"], "confidence": 0}

    # Cache the result
    if "error" not in result.get("answer", ""):
        cache[cache_key] = result
        _save_cache(cache)

    return result


def _describe_tabular(question: str, doc: dict) -> dict:
    """Generate a descriptive summary of a tabular dataset."""
    stored_path = os.path.join(DOCS_DIR, doc["stored_filename"])
    try:
        if doc["type"] == "csv":
            df = pd.read_csv(stored_path)
        else:
            df = pd.read_excel(stored_path)

        rows, cols = df.shape
        columns = list(df.columns)
        sample = df.head(5).to_string()

        # Describe numeric and categorical columns
        desc = df.describe(include="all").to_string()

        system = """You are a helpful data analyst. The user wants to understand what a dataset contains.
Provide a clear, structured summary covering:
1. What type of data this is
2. Number of rows and columns
3. Key columns and what they represent
4. Notable patterns (missing values, data ranges, common values)
Be concise but thorough. Use bullet points."""

        prompt = f"""Dataset: {doc['filename']}
Shape: {rows} rows × {cols} columns
Columns: {columns}

Sample data:
{sample}

Statistics:
{desc}

User question: {question}

Provide a detailed answer:"""

        answer = query_llm(prompt, system)
        return {
            "answer": answer,
            "source": doc["filename"],
            "confidence": 0.9,
        }
    except Exception as e:
        return {"answer": f"⚠️ Error: {str(e)}", "source": doc["filename"], "confidence": 0}


def _generate_chart(result, question: str, doc_id: str) -> Optional[str]:
    """Auto-generate a chart from query results. Returns chart file path or None."""
    try:
        # Only chart DataFrames and Series with reasonable size
        if isinstance(result, (int, float, str)):
            return None

        if isinstance(result, pd.Series):
            if len(result) < 2 or len(result) > 50:
                return None
            df_chart = result.reset_index()
            df_chart.columns = ["Category", "Value"]
        elif isinstance(result, pd.DataFrame):
            if len(result) < 2 or len(result) > 50:
                return None
            # Use first two columns as category + value
            if len(result.columns) < 2:
                return None
            # Find a numeric column
            num_cols = result.select_dtypes(include='number').columns.tolist()
            cat_cols = result.select_dtypes(exclude='number').columns.tolist()
            if not num_cols or not cat_cols:
                # Try using index as category
                if num_cols:
                    df_chart = result[[num_cols[0]]].reset_index()
                    df_chart.columns = ["Category", "Value"]
                else:
                    return None
            else:
                df_chart = result[[cat_cols[0], num_cols[0]]].copy()
                df_chart.columns = ["Category", "Value"]
        else:
            return None

        # Ensure Value is numeric
        df_chart["Value"] = pd.to_numeric(df_chart["Value"], errors="coerce")
        df_chart = df_chart.dropna(subset=["Value"])
        if len(df_chart) < 2:
            return None

        # Convert Category to string for labels
        df_chart["Category"] = df_chart["Category"].astype(str)
        # Truncate long labels
        df_chart["Category"] = df_chart["Category"].apply(lambda x: x[:25] + "..." if len(x) > 25 else x)

        # ── Auto-detect chart type ──
        q = question.lower()
        n_cats = len(df_chart)

        if any(k in q for k in ["pie", "proportion", "percentage", "share", "distribution", "breakdown"]):
            chart_type = "pie"
        elif any(k in q for k in ["trend", "over time", "timeline", "monthly", "yearly", "daily"]):
            chart_type = "line"
        elif any(k in q for k in ["histogram", "frequency", "spread"]):
            chart_type = "histogram"
        elif n_cats <= 8 and all(df_chart["Value"] >= 0):
            # Small category count with positive values → pie or bar
            chart_type = "pie" if n_cats <= 6 else "bar"
        else:
            chart_type = "bar"

        # ── Dark theme styling ──
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(8, 5))
        fig.patch.set_facecolor('#1a1a2e')
        ax.set_facecolor('#1a1a2e')

        # Color palette
        colors = ['#7c3aed', '#a855f7', '#c084fc', '#d8b4fe', '#e9d5ff',
                  '#6366f1', '#818cf8', '#a5b4fc', '#c7d2fe', '#e0e7ff']

        if chart_type == "bar":
            bars = ax.barh(df_chart["Category"], df_chart["Value"], color=colors[:n_cats], edgecolor='none')
            ax.invert_yaxis()
            ax.set_xlabel("Value", color='#e2e8f0', fontsize=11)
            ax.tick_params(colors='#e2e8f0', labelsize=10)
            ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{x:,.0f}' if x >= 1000 else f'{x:g}'))
            # Add value labels on bars
            for bar_item, val in zip(bars, df_chart["Value"]):
                ax.text(bar_item.get_width() + max(df_chart["Value"]) * 0.01, bar_item.get_y() + bar_item.get_height() / 2,
                        f'{val:,.0f}' if val >= 100 else f'{val:g}',
                        va='center', color='#e2e8f0', fontsize=9)

        elif chart_type == "pie":
            wedges, texts, autotexts = ax.pie(
                df_chart["Value"], labels=df_chart["Category"],
                autopct='%1.1f%%', colors=colors[:n_cats],
                textprops={'color': '#e2e8f0', 'fontsize': 10},
                pctdistance=0.75, startangle=90
            )
            for t in autotexts:
                t.set_fontsize(9)
                t.set_color('#ffffff')

        elif chart_type == "line":
            ax.plot(df_chart["Category"], df_chart["Value"], color='#a855f7', linewidth=2.5, marker='o', markersize=6)
            ax.fill_between(range(len(df_chart)), df_chart["Value"], alpha=0.15, color='#a855f7')
            ax.set_xlabel("Category", color='#e2e8f0', fontsize=11)
            ax.set_ylabel("Value", color='#e2e8f0', fontsize=11)
            ax.tick_params(colors='#e2e8f0', labelsize=9)
            plt.xticks(rotation=45, ha='right')

        elif chart_type == "histogram":
            ax.hist(df_chart["Value"], bins=min(15, n_cats), color='#7c3aed', edgecolor='#1a1a2e', alpha=0.85)
            ax.set_xlabel("Value", color='#e2e8f0', fontsize=11)
            ax.set_ylabel("Frequency", color='#e2e8f0', fontsize=11)
            ax.tick_params(colors='#e2e8f0', labelsize=10)

        # Title from question
        title = question[:60] + "..." if len(question) > 60 else question
        ax.set_title(title, color='#f8fafc', fontsize=13, fontweight='bold', pad=15)

        # Remove spines for clean look
        for spine in ax.spines.values():
            spine.set_color('#334155')
            spine.set_linewidth(0.5)

        plt.tight_layout()

        # Save chart
        chart_filename = f"chart_{doc_id[:8]}_{uuid.uuid4().hex[:6]}.png"
        chart_path = os.path.join(CHARTS_DIR, chart_filename)
        fig.savefig(chart_path, dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor())
        plt.close(fig)

        logger.info(f"📊 Chart generated: {chart_path}")
        return chart_path

    except Exception as e:
        logger.warning(f"Chart generation failed (non-fatal): {e}")
        plt.close('all')  # Prevent figure leaks
        return None


def _query_tabular(question: str, doc: dict) -> dict:
    """Query CSV/Excel data by generating and executing pandas code."""
    stored_path = os.path.join(DOCS_DIR, doc["stored_filename"])
    if not os.path.exists(stored_path):
        return {"answer": "⚠️ File not found on disk.", "source": doc["filename"], "confidence": 0}

    try:
        if doc["type"] == "csv":
            df = pd.read_csv(stored_path)
        else:
            df = pd.read_excel(stored_path)

        columns = list(df.columns)
        dtypes = {col: str(df[col].dtype) for col in columns}
        sample = df.head(3).to_string()

        # ── Build conversation context from session history ──
        history = _get_session(doc["id"])
        history_context = ""
        if history:
            history_context = "\n\nPREVIOUS QUERIES IN THIS SESSION (use as context for follow-up questions):\n"
            for i, h in enumerate(history, 1):
                history_context += f"Q{i}: {h['question']}\nCode: {h['code']}\nResult: {h['result']}\n\n"

        system_prompt = """You are a data analyst. Given a pandas DataFrame description and a user question,
generate ONLY the Python pandas code to answer the question.
Output ONLY the code, no explanation. The DataFrame is stored in variable `df`.
The code must produce a variable called `result` that contains the answer.
If the answer is a DataFrame, assign it to `result`. If it's a single value, assign it to `result`.
Do NOT use print(). Do NOT import anything. Do NOT modify df.

CRITICAL RULES:
- Use exact column names as given. Column names are case-sensitive.
- For TEXT column filtering, ALWAYS use `.str.contains('keyword', case=False, na=False)` instead of `==`.
  Example: `df[df['Job Title'].str.contains('engineer', case=False, na=False)]` NOT `df[df['Job Title'] == 'Engineer']`
- For DATE columns, convert with `pd.to_datetime(df['col'], errors='coerce')` before comparing.
- For "top N" questions, use `.nlargest(N, 'column')` or `.head(N)` after sorting.
- For aggregations, prefer `.groupby().agg()` and reset_index().
- If the user asks a follow-up question, use the previous query context to understand what they're referring to."""

        code_prompt = f"""DataFrame info:
Columns: {columns}
Data types: {dtypes}
Sample rows:
{sample}
{history_context}
User question: {question}

Generate pandas code (assign answer to `result`):"""

        # Get pandas code from LLM
        code = query_llm(code_prompt, system_prompt)

        # Clean the code — strip markdown fences
        code = code.strip()
        if code.startswith("```"):
            lines = code.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            code = "\n".join(lines)
        code = code.strip()

        # Execute safely
        exec_globals = {"df": df, "pd": pd}
        exec(code, exec_globals)
        result = exec_globals.get("result", "No result computed.")

        # ── Generate chart if applicable ──
        chart_path = _generate_chart(result, question, doc["id"])

        # Format the result
        if isinstance(result, pd.DataFrame):
            result_summary = result.head(10).to_string()
            if len(result) > 20:
                answer = f"📊 Result ({len(result)} rows, showing first 20):\n```\n{result.head(20).to_string()}\n```"
            else:
                answer = f"📊 Result:\n```\n{result.to_string()}\n```"
        elif isinstance(result, pd.Series):
            result_summary = result.to_string()
            answer = f"📊 Result:\n```\n{result.to_string()}\n```"
        else:
            result_summary = str(result)
            answer = f"📊 Answer: **{result}**"

        # ── Save to session for follow-up queries ──
        _add_to_session(doc["id"], question, code, result_summary)

        resp = {
            "answer": answer,
            "source": f"{doc['filename']}",
            "confidence": 0.95,
            "code_used": code,
        }
        if chart_path:
            resp["chart_path"] = chart_path
        return resp

    except Exception as e:
        logger.error(f"Tabular query error: {e}")
        # Fallback: ask LLM to answer directly from sample data
        return _query_tabular_fallback(question, doc, str(e))


def _query_tabular_fallback(question: str, doc: dict, error: str) -> dict:
    """If code execution fails, try to answer from data description."""
    stored_path = os.path.join(DOCS_DIR, doc["stored_filename"])
    try:
        if doc["type"] == "csv":
            df = pd.read_csv(stored_path)
        else:
            df = pd.read_excel(stored_path)

        desc = df.describe(include="all").to_string()
        sample = df.head(5).to_string()

        prompt = f"""I have a dataset called "{doc['filename']}".
Here are statistics:
{desc}

Sample data:
{sample}

User asked: "{question}"

The automated query failed with error: {error}
Please answer the question based on the data statistics and sample above.
Be specific with numbers when possible. If you can't answer precisely, say so."""

        system = "You are a helpful data analyst. Answer concisely with numbers when available. Respond in Hinglish if the question is in Hinglish."

        answer = query_llm(prompt, system)
        return {
            "answer": answer,
            "source": doc["filename"],
            "confidence": 0.7,
            "note": "Answered from data summary (code execution failed)",
        }
    except Exception as e:
        return {
            "answer": f"⚠️ Could not analyze the data: {str(e)}",
            "source": doc["filename"],
            "confidence": 0,
        }


def _is_descriptive_question(question: str) -> bool:
    """Detect if the question is asking for a summary/description rather than a specific fact."""
    q = question.lower().strip()
    descriptive_patterns = [
        "what is this", "what's this", "what is the document", "what's the document",
        "summarize", "summarise", "summary", "describe", "description",
        "what does this", "what does the", "what is it about", "what's it about",
        "overview", "tell me about", "batao", "kya hai", "iske baare",
        "explain", "outline", "main points", "key points", "highlights",
        "about this", "about the document", "about the file",
        "what are the contents", "content of this", "what info", "what information",
        "whats this", "what this", "kya likha", "kya batata",
    ]
    return any(p in q for p in descriptive_patterns)


def _read_full_document_text(doc: dict) -> str:
    """Read the full text content from a stored PDF or text document."""
    stored_path = os.path.join(DOCS_DIR, doc["stored_filename"])
    if not os.path.exists(stored_path):
        return ""

    if doc["type"] == "pdf":
        try:
            import pdfplumber
            texts = []
            with pdfplumber.open(stored_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        texts.append(text.strip())
            return "\n\n".join(texts)
        except Exception:
            return ""
    else:
        try:
            with open(stored_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""


def _query_text_rag(question: str, doc: dict) -> dict:
    """Query PDF/text documents using ChromaDB vector search + LLM.
    For descriptive/summary questions, reads full text instead of RAG search."""

    # ── Descriptive question: summarize the full document ──
    if _is_descriptive_question(question):
        full_text = _read_full_document_text(doc)
        if not full_text:
            return {"answer": "⚠️ Could not read the document text.", "source": doc["filename"], "confidence": 0}

        # Limit text to ~3000 words to avoid LLM token limits
        words = full_text.split()
        if len(words) > 3000:
            text_for_llm = " ".join(words[:3000]) + f"\n\n... [truncated, {len(words)} total words]"
        else:
            text_for_llm = full_text

        system = """You are a helpful document analyst. The user wants to understand what a document contains.
Provide a clear, structured summary covering:
1. What type of document this is (receipt, report, contract, etc.)
2. Key information found (names, dates, amounts, topics)
3. Main purpose of the document
Be concise but thorough. Use bullet points. Respond in Hinglish if the question is in Hinglish, otherwise English."""

        prompt = f"""Document: {doc['filename']}

Full text content:
{text_for_llm}

User question: {question}

Provide a detailed answer:"""

        answer = query_llm(prompt, system)
        return {
            "answer": answer,
            "source": doc["filename"],
            "confidence": 0.9,
        }

    # ── Specific factual question: use RAG search ──
    try:
        import chromadb
        client = chromadb.PersistentClient(path=os.path.join(BASE_DIR, "chroma_db"))
        collection_name = f"analyst_{doc['id'][:8]}"

        try:
            collection = client.get_collection(name=collection_name)
        except Exception:
            return {"answer": "⚠️ Document index not found. Try re-uploading.", "source": doc["filename"], "confidence": 0}

        # Search for relevant chunks
        results = collection.query(query_texts=[question], n_results=5)

        if not results["documents"] or not results["documents"][0]:
            return {"answer": "⚠️ No relevant sections found in the document for this question.", "source": doc["filename"], "confidence": 0.1}

        chunks = results["documents"][0]
        distances = results["distances"][0] if results.get("distances") else []

        # If RAG distances are too high, fall back to full-doc summary
        if distances and all(d > 1.5 for d in distances):
            full_text = _read_full_document_text(doc)
            if full_text:
                words = full_text.split()
                text_for_llm = " ".join(words[:3000]) if len(words) > 3000 else full_text

                system = """You are a helpful document analyst. Answer the user's question from the document.
If the answer is not clearly in the document, explain what the document DOES contain instead.
Respond in Hinglish if the question is in Hinglish."""

                prompt = f"""Document: {doc['filename']}

Document content:
{text_for_llm}

Question: {question}

Answer:"""
                answer = query_llm(prompt, system)
                return {
                    "answer": answer,
                    "source": doc["filename"],
                    "confidence": 0.7,
                }

        context = "\n---\n".join(chunks)

        system = """You are a helpful business analyst. Answer the user's question based ONLY on the provided document context.
If the answer is not in the context, say you couldn't find it.
Include specific quotes or numbers from the context when possible.
Respond in Hinglish if the question is in Hinglish, otherwise respond in English."""

        prompt = f"""Document: {doc['filename']}

Relevant sections:
{context}

Question: {question}

Answer based on the document above:"""

        answer = query_llm(prompt, system)

        avg_confidence = 1.0 - (sum(distances) / len(distances)) if distances else 0.5

        return {
            "answer": answer,
            "source": f"{doc['filename']}",
            "confidence": round(max(0, min(1, avg_confidence)), 2),
        }

    except Exception as e:
        logger.error(f"RAG query error: {e}")
        return {"answer": f"⚠️ Error querying document: {str(e)}", "source": doc["filename"], "confidence": 0}


# ─── CONVENIENCE FUNCTIONS ───────────────────────────────────────────────────

def get_active_document_summary() -> str:
    """Get a summary of the most recently uploaded document."""
    index = _load_index()
    if not index:
        return "No documents uploaded yet."
    doc = index[-1]
    return doc.get("summary", f"Document: {doc['filename']}")


def get_document_by_id(doc_id: str) -> Optional[dict]:
    """Get document metadata by ID."""
    index = _load_index()
    return next((d for d in index if d["id"] == doc_id), None)
