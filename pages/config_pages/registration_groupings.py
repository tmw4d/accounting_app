import database as db
import streamlit as st


def render():
    st.header("Registration Groupings")
    st.write("Manage the grouping choices used for TopScore products.")

    with st.form("add_registration_grouping", clear_on_submit=True):
        name = st.text_input("New grouping")
        if st.form_submit_button("Add grouping"):
            cleaned_name = name.strip()
            if not cleaned_name:
                st.error("Enter a grouping name.")
            else:
                with db.get_connection() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO registration_groupings (name) VALUES (?)",
                        (cleaned_name,),
                    )
                st.rerun()

    with db.get_connection() as conn:
        groupings = [
            row[0] for row in conn.execute(
                "SELECT name FROM registration_groupings ORDER BY name"
            ).fetchall()
        ]

    if not groupings:
        st.info("No registration groupings have been configured.")
        return

    grouping_to_remove = st.selectbox("Grouping to remove", groupings)
    if st.button("Remove grouping"):
        with db.get_connection() as conn:
            mapped_count = conn.execute(
                "SELECT COUNT(*) FROM topscore_product_mappings WHERE grouping = ?",
                (grouping_to_remove,),
            ).fetchone()[0]
            if mapped_count:
                st.error(
                    f"Cannot remove {grouping_to_remove!r}; it is assigned to {mapped_count:,} product(s). "
                    "Reassign those products first."
                )
            else:
                conn.execute(
                    "DELETE FROM registration_groupings WHERE name = ?",
                    (grouping_to_remove,),
                )
                st.rerun()

    st.dataframe({"Grouping": groupings}, hide_index=True, width="stretch")
