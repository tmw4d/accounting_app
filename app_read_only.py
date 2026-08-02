import streamlit as st
import app

# Set session state before running main app logic
st.session_state["read_only_mode"] = True

if __name__ == "__main__":
    app.main()
