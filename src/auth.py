"""Shared password protection and UI cleanup for all pages."""

import streamlit as st

HIDE_ELEMENTS = """
<style>
    .stDeployButton { display: none !important; }
    [data-testid="stDecoration"] { display: none !important; }
</style>
<script>
function hideGitHub() {
    // Hide Fork/GitHub buttons (they load as iframes or links)
    document.querySelectorAll('iframe').forEach(el => {
        if ((el.title || '').toLowerCase().includes('github') ||
            (el.src || '').includes('github')) {
            el.style.display = 'none';
        }
    });
    // Hide any element containing "Fork" text in the toolbar area
    const toolbar = document.querySelector('[data-testid="stToolbar"]') ||
                    document.querySelector('.stAppToolbar');
    if (toolbar) {
        toolbar.querySelectorAll('a, button, span').forEach(el => {
            const text = el.textContent || '';
            const href = el.getAttribute('href') || '';
            if (text.includes('Fork') || href.includes('github.com')) {
                el.style.display = 'none';
            }
        });
    }
}
// Run immediately and keep checking (elements load dynamically)
hideGitHub();
const observer = new MutationObserver(hideGitHub);
observer.observe(document.body, { childList: true, subtree: true });
// Stop observing after 10 seconds to save resources
setTimeout(() => observer.disconnect(), 10000);
</script>
"""


def check_password():
    """Returns True if the user has entered the correct password."""
    st.markdown(HIDE_ELEMENTS, unsafe_allow_html=True)

    if st.session_state.get("authenticated", False):
        return True

    st.markdown("""
    <div style="max-width: 400px; margin: 15vh auto; text-align: center;">
        <h2 style="color: #1B4D3E;">🏦 CLO Monitor</h2>
        <p style="color: #666;">Enter password to access the dashboard</p>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        password = st.text_input("Password", type="password", key="pw_input")
        if st.button("Enter", use_container_width=True):
            try:
                correct = st.secrets["password"]
            except (FileNotFoundError, KeyError):
                correct = "clo2026"

            if password == correct:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password")

    return False
