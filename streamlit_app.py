"""
streamlit_app.py — Streamlit UI for "Chat with your PDFs" (RAG demo).

Run it with:
    streamlit run streamlit_app.py

Upload one or more PDFs, click "Build knowledge base", then ask questions.
Answers are grounded in your documents and cite their sources.

Uses Groq's free API by default. Paste your Groq key in the sidebar
(get one free at console.groq.com). Local Ollama is also supported via env vars.
"""

import os

import streamlit as st

from rag import KnowledgeBase, read_pdf, split_chunks, answer_question, list_models, LLM_MODEL

st.set_page_config(page_title="Chat with your PDFs", page_icon="📄", layout="centered")

st.title("📄 Chat with your PDFs")
st.caption(
    "A Retrieval-Augmented Generation (RAG) demo: ask questions and get answers "
    "grounded in your own documents, with sources cited."
)

# --- Sidebar: settings ----------------------------------------------------
with st.sidebar:
    st.header("Settings")
    api_key = st.text_input(
        "Groq API key",
        type="password",
        value=os.environ.get("LLM_API_KEY", ""),
        help="Paste your Groq key (free at console.groq.com). Used only in this session.",
    )
    model = LLM_MODEL
    if api_key:
        try:
            available = list_models(api_key)
            if available:
                default = next((m for m in available
                                if "versatile" in m or "70b" in m or "instant" in m),
                               available[0])
                model = st.selectbox("Model", available, index=available.index(default))
            else:
                model = st.text_input("Model", value=LLM_MODEL)
        except Exception:
            model = st.text_input(
                "Model", value=LLM_MODEL,
                help="Couldn't fetch the model list; type a valid model id.")
    else:
        st.caption(f"Default model: {LLM_MODEL}")
    top_k = st.slider("Passages to retrieve", min_value=2, max_value=8, value=4)
    st.markdown("---")
    st.markdown(
        "**How it works**\n\n"
        "1. Your PDFs are split into passages.\n"
        "2. Each passage is embedded locally (free).\n"
        "3. Your question finds the closest passages.\n"
        "4. The model answers using only those passages."
    )

# --- Load the embedding model once, cached across reruns ------------------
@st.cache_resource(show_spinner="Loading the embedding model (first run only)...")
def load_kb() -> KnowledgeBase:
    return KnowledgeBase()


# --- Main: upload and build ----------------------------------------------
uploaded = st.file_uploader(
    "Upload one or more PDFs", type=["pdf"], accept_multiple_files=True
)

if st.button("Build knowledge base", type="primary", disabled=not uploaded):
    kb = load_kb()
    all_pages = []
    for f in uploaded:
        all_pages.extend(read_pdf(f, f.name))
    chunks = split_chunks(all_pages)
    if not chunks:
        st.error("No readable text found. Is the PDF scanned (image-only)?")
    else:
        with st.spinner(f"Embedding {len(chunks)} passages..."):
            kb.build(chunks)
        st.session_state["kb_ready"] = True
        st.session_state["num_chunks"] = len(chunks)
        st.success(f"Ready. Indexed {len(chunks)} passages from {len(uploaded)} file(s).")

# --- Ask questions --------------------------------------------------------
if st.session_state.get("kb_ready"):
    st.markdown("---")
    question = st.text_input("Ask a question about your documents")
    if question:
        if not api_key:
            st.warning("Enter your Groq API key in the sidebar to get answers.")
            st.stop()
        kb = load_kb()
        with st.spinner("Searching your documents and thinking..."):
            passages = kb.retrieve(question, k=top_k)
            try:
                reply = answer_question(question, passages, api_key, model)
            except Exception as e:  # surface errors clearly
                st.error(f"Could not get an answer: {e}")
                reply = None
        if reply:
            st.markdown("### Answer")
            st.write(reply)
            with st.expander("Show the passages used"):
                for p in passages:
                    st.markdown(f"**{p.source}**")
                    st.write(p.text)
else:
    st.info("Upload PDFs and build the knowledge base to start asking questions.")
