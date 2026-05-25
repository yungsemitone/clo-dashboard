"""Shared password protection for all pages."""

import streamlit as st


def check_password():
    """Returns True if the user has entered the correct password."""
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
                correct = "clo2026"  # default fallback

            if password == correct:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password")

    return False
