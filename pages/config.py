import streamlit as st
from pages.config_pages import allocations, categories, fiscal_years, programs, topscore_products

def render():
    st.title("System Configuration")
    
    # Simple navigation within the config section
    menu = st.radio("Select configuration module:", 
                    ["Categories", "Fiscal Years", "Programs", "Allocation Methods", "TopScore Products"], 
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
    elif menu == "TopScore Products":
        topscore_products.render()
