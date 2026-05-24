import streamlit as st
from pages.config_pages import categories, fiscal_years, programs, allocations

def render():
    st.title("System Configuration")
    
    # Simple navigation within the config section
    menu = st.radio("Select configuration module:", 
                    ["Categories", "Fiscal Years", "Programs", "Allocation Methods"], 
                    horizontal=True)
    
    st.divider()
    
    if menu == "Categories":
        categories.render()
    elif menu == "Fiscal Years":
        fiscal_years.render()
    elif menu == "Programs":
        programs.render()
    elif menu == "Allocation Methods":
        allocations.render()
