"""Power Interpreter – Sandbox Code Execution Engine

Runs user-submitted Python inside a persistent kernel with:
  • Variable / import persistence across calls
  • Curated import allow-list (data-science + doc-gen stack)
  • Automatic chart capture (matplotlib, seaborn, plotly)
  • File output stored to Postgres with public download URLs
  • Path normalisation (sandbox prefix, /tmp interception, dedup)
  • Read-only access to SimTheory upload directory
  • Memory-guarded subprocess isolation

Author: Kaffer AI for Timothy Escamilla
Version: 2.9.0

CHANGELOG (engine-layer – now unified with MCP server version):
  v2.0.0  Persistent session state (kernel architecture)
  v2.1.0  Auto file storage in Postgres with download URLs
  v2.6.0  Inline chart rendering (matplotlib/plotly auto-capture)
  v2.7.0  reportlab + matplotlib PDF backend support
  v2.8.0  Defensive path normalisation (session prefix doubling fix)
  v2.8.1  /tmp/ path interception → redirect to sandbox
  v2.8.2  Read-only upload access (sandbox reads SimTheory uploads)
  v2.8.3  /app/sandbox_data recognised in allowed read paths
  v2.8.4  datetime module injection fix (MODULE, not class)
  v2.8.5  python-docx + transitive deps (zipfile, lxml, xml …)
  v2.8.6  Timeout floor (100 s minimum), version unification
  v2.9.0  Trimmed MCP tool descriptions for token optimisation (~57% reduction)
"""
