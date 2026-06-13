from __future__ import annotations

DESIGN_TOKENS = {
    "color_primary": "#4F46E5",
    "color_success": "#10B981",
    "color_warning": "#F59E0B",
    "color_danger": "#EF4444",
    "font_size_sm": "12px",
    "font_size_base": "14px",
    "font_size_lg": "16px",
    "border_radius": "8px",
    "shadow_sm": "0 1px 3px rgba(0,0,0,0.1)",
}

SHARED_CSS = """
<style>
/* Premium Paper Style */
.qb-paper-card {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 18px 22px;
    margin-bottom: 14px;
    box-shadow: 0 4px 15px rgba(15, 23, 42, 0.05);
    font-family: 'Times New Roman', SimSun, serif;
}

.qb-paper-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px dashed #cbd5e1;
    padding-bottom: 8px;
    margin-bottom: 10px;
    font-size: 0.86rem;
    color: #64748b;
}

.qb-paper-title-tag {
    font-weight: bold;
    font-size: 0.98rem;
    color: #1e293b;
}

.qb-rich-text {
    white-space: pre-wrap;
    line-height: 1.62;
    font-size: 0.96rem;
    color: #0f172a;
    margin: 0.25rem 0 0.6rem 0;
}

.qb-rich-text sub {
    font-size: 70%;
    vertical-align: sub;
    line-height: 0;
}
.qb-rich-text sup {
    font-size: 70%;
    vertical-align: super;
    line-height: 0;
}
.qb-rich-text u {
    text-decoration: underline;
    text-underline-offset: 3px;
    text-decoration-thickness: 1.5px;
}

.qb-rich-text table {
    border-collapse: collapse;
    width: auto;
    max-width: 100%;
    margin: 0.35rem 0 0.65rem;
    table-layout: auto;
}

.qb-rich-text td,
.qb-rich-text th {
    border: 1px solid #d8dee9;
    padding: 0.32rem 0.45rem;
    vertical-align: top;
    word-break: break-word;
}

.qb-rich-text tr:nth-child(even) {
    background: #f8fafc;
}

/* Badge styling */
.qb-badge {
    display: inline-flex;
    align-items: center;
    border-radius: 9999px;
    padding: 2px 10px;
    font-size: 0.75rem;
    font-weight: 600;
    line-height: 1.2;
    margin-right: 6px;
    margin-bottom: 4px;
}

.qb-badge-easy {
    background-color: #f0fdf4;
    color: #166534;
    border: 1px solid #bbf7d0;
}

.qb-badge-medium {
    background-color: #fef8e6;
    color: #854d0e;
    border: 1px solid #fef08a;
}

.qb-badge-hard {
    background-color: #fef2f2;
    color: #991b1b;
    border: 1px solid #fecaca;
}

.qb-badge-blue {
    background-color: #eff6ff;
    color: #1d4ed8;
    border: 1px solid #bfdbfe;
}

.qb-badge-green {
    background-color: #ecfdf5;
    color: #047857;
    border: 1px solid #a7f3d0;
}

.qb-badge-orange {
    background-color: #fff7ed;
    color: #c2410c;
    border: 1px solid #ffedd5;
}

.qb-badge-purple {
    background-color: #faf5ff;
    color: #6b21a8;
    border: 1px solid #e9d5ff;
}

.qb-badge-magenta {
    background-color: #fdf2f8;
    color: #be185d;
    border: 1px solid #fbcfe8;
}

.qb-badge-gray {
    background-color: #f8fafc;
    color: #475569;
    border: 1px solid #e2e8f0;
}

.qb-badge-red {
    background-color: #fef2f2;
    color: #dc2626;
    border: 1px solid #fecaca;
}

/* Teacher Mode Box */
.qb-teacher-box {
    background-color: #f8fafc;
    border-left: 4px solid #3b82f6;
    padding: 10px 14px;
    margin-top: 10px;
    border-radius: 0 6px 6px 0;
    font-size: 0.88rem;
}

.qb-teacher-title {
    font-weight: bold;
    color: #1e3a8a;
    margin-bottom: 6px;
    display: flex;
    align-items: center;
    gap: 6px;
}

/* Tag Panel inside Card */
.qb-tag-panel {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    margin: 0.75rem 0;
}
.qb-tag-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.35rem;
}
.qb-tag-label {
    min-width: 4.75rem;
    color: #697182;
    font-size: 0.9rem;
    font-weight: 600;
}
.qb-tag-chip {
    display: inline-flex;
    align-items: center;
    border: 1px solid #d9dee8;
    border-radius: 999px;
    padding: 0.12rem 0.55rem;
    background: #f6f8fb;
    color: #303642;
    font-size: 0.86rem;
    line-height: 1.55;
}

/* Fixed/Floating Basket panel */
.qb-fixed-basket-shell {
    position: fixed;
    right: 1.35rem;
    top: 50%;
    transform: translateY(-50%);
    z-index: 999999;
    font-family: "Microsoft YaHei", sans-serif;
}
.qb-fixed-basket-details {
    position: relative;
}
.qb-fixed-basket-button {
    width: 3.5rem;
    height: 3.5rem;
    border-radius: 999px;
    background: #ffffff;
    border: 1px solid rgba(49, 51, 63, 0.18);
    box-shadow: 0 8px 24px rgba(20, 25, 40, 0.18);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    list-style: none;
    position: relative;
}
.qb-fixed-basket-button::-webkit-details-marker {
    display: none;
}
.qb-fixed-basket-icon {
    font-size: 1.45rem;
}
.qb-fixed-basket-badge {
    position: absolute;
    right: -0.15rem;
    top: -0.2rem;
    min-width: 1.35rem;
    height: 1.35rem;
    padding: 0 0.28rem;
    border-radius: 999px;
    background: #ef4444;
    color: #fff;
    font-size: 0.78rem;
    line-height: 1.35rem;
    text-align: center;
    font-weight: 700;
}
.qb-fixed-basket-panel {
    position: absolute;
    right: 4.2rem;
    top: 50%;
    transform: translateY(-50%);
    width: min(22rem, calc(100vw - 6rem));
    max-height: 68vh;
    overflow: hidden;
    border: 1px solid #d9dee8;
    border-radius: 0.75rem;
    background: #ffffff;
    box-shadow: 0 16px 40px rgba(20, 25, 40, 0.2);
    padding: 0.8rem;
}
.qb-fixed-basket-title {
    font-size: 1rem;
    font-weight: 700;
    margin-bottom: 0.55rem;
    color: #242936;
}
.qb-fixed-basket-list {
    max-height: 48vh;
    overflow-y: auto;
    padding-right: 0.25rem;
}
.qb-fixed-basket-item {
    border-bottom: 1px solid #edf0f5;
    padding: 0.55rem 0;
}
.qb-fixed-basket-source {
    color: #252b37;
    font-weight: 650;
    font-size: 0.9rem;
}
.qb-fixed-basket-text,
.qb-fixed-basket-empty {
    color: #667085;
    font-size: 0.82rem;
    line-height: 1.45;
    margin: 0.25rem 0;
}
.qb-fixed-basket-remove {
    color: #ef4444;
    font-size: 0.82rem;
    text-decoration: none;
}
.qb-fixed-basket-clear {
    display: block;
    margin-top: 0.65rem;
    border-radius: 0.45rem;
    border: 1px solid #fecaca;
    color: #dc2626 !important;
    text-align: center;
    padding: 0.48rem 0.7rem;
    font-weight: 650;
    text-decoration: none;
    background: #fff7f7;
}
.qb-fixed-basket-go {
    display: block;
    margin-top: 0.7rem;
    border-radius: 0.45rem;
    background: #1f6feb;
    color: #fff !important;
    text-align: center;
    padding: 0.55rem 0.7rem;
    font-weight: 650;
    text-decoration: none;
}
</style>
"""


def inject_shared_css(st) -> None:
    st.markdown(SHARED_CSS, unsafe_allow_html=True)
