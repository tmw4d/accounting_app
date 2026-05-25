# Accounting App

A streamlined, robust accounting solution built for personal and small-group financial tracking. This application leverages **Streamlit** for a modern interface and **SQLite** for reliable, lightweight data management.

---

## 🚀 Getting Started

### Prerequisites
* **Python 3.12+**
* **uv** (Recommended for fast dependency management)

### Installation

1.  **Clone the repository:**
    ```bash
    git clone <your-repo-url>
    cd accounting_app
    ```

2.  **Create and activate the virtual environment:**
    ```bash
    uv venv
    source .venv/bin/activate  # macOS/Linux
    # OR .venv\Scripts\activate # Windows
    ```

3.  **Install dependencies:**
    ```bash
    uv pip install -r requirements.txt
    ```

---

## 🛠 Project Structure

* `app.py`: Main entry point for the dashboard and navigation.
* `pages/`: Contains the configuration router and sub-pages.
* `pages/config_pages/`: Modular configuration modules (Categories, Programs, Allocations).
* `data/`: Directory for the SQLite database (`ledger.db`).
* `.gitignore`: Prevents database files and environment configurations from being tracked.

---

## 📊 Running the App

Launch the application locally using Streamlit:

```bash
streamlit run app.py