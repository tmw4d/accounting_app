import streamlit as st
import database as db

# Initialize the system
db.init_db()

st.sidebar.title("Accounting System")
page = st.sidebar.radio("Navigate to:", [
    "Dashboard", 
    "Manual Entry", 
    "Import Bank Files", 
    "Configuration"
])

if page == "Dashboard":
    st.title("Welcome")
    st.write("Use the sidebar to manage your accounts.")
elif page == "Manual Entry":
    from pages import entries
    entries.render()
elif page == "Import Bank Files":
    from pages import imports
    imports.render()
elif page == "Configuration":
    from pages import config
    config.render()