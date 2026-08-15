import duckdb
import streamlit as st
import plotly.express as px
import pandas as pd
from ingest import config

# Set page config
st.set_page_config(page_title="Workout Analytics", layout="wide")

# Connect to DuckDB
@st.cache_resource
def get_connection():
    # Streamlit connects read-only so it doesn't block the ingest pipeline
    return duckdb.connect(str(config.duckdb_path()), read_only=True)

con = get_connection()

st.title("Workout Analytics - HIT Performance")

# Check if the table exists first
try:
    con.execute("SELECT 1 FROM marts.obt_sets LIMIT 1")
except duckdb.Error:
    st.error("marts.obt_sets table not found. Please run `make pipeline` first.")
    st.stop()

# Query the data
@st.cache_data
def load_data():
    return con.execute("""
        SELECT *
        FROM marts.obt_sets
        WHERE training_era = 'hit'
    """).df()

df = load_data()

# Filters
st.sidebar.header("Filters")
patterns = df['movement_pattern'].dropna().unique().tolist()
selected_pattern = st.sidebar.selectbox("Movement Pattern", ["All"] + patterns)

if selected_pattern != "All":
    df_filtered = df[df['movement_pattern'] == selected_pattern]
else:
    df_filtered = df

exercises = df_filtered['canonical_exercise'].dropna().unique().tolist()
selected_exercise = st.sidebar.selectbox("Canonical Exercise", ["All"] + exercises)

if selected_exercise != "All":
    df_filtered = df_filtered[df_filtered['canonical_exercise'] == selected_exercise]

st.markdown("---")

col1, col2 = st.columns(2)

# Create a dataframe just for the charts that drops null relative_performance
df_charts = df_filtered.dropna(subset=['relative_performance']).copy()

with col1:
    st.subheader("Recovery Curve")
    st.markdown("Relative Performance vs. Days Since Last Exposure")
    
    # Bucket the rest days to reduce noise in the tail
    df_charts['rest_bucket'] = df_charts['days_since_last_exposure'].apply(
        lambda x: "7+" if x >= 7 else str(int(x)) if not pd.isna(x) else None
    )
    
    if not df_charts.empty:
        fig1 = px.scatter(
            df_charts, 
            x="days_since_last_exposure", 
            y="relative_performance",
            color="canonical_exercise" if selected_pattern != "All" else "movement_pattern",
            hover_data=["workout_date_local", "working_e1rm_lb", "trailing_90d_max_e1rm_lb"],
            trendline="ols" if len(df_charts) > 3 else None
        )
        st.plotly_chart(fig1, use_container_width=True)
    else:
        st.info("Not enough data to display.")

with col2:
    st.subheader("Time of Day Impact")
    st.markdown("Relative Performance vs. Hour of Day")
    
    if not df_charts.empty:
        fig2 = px.box(
            df_charts,
            x="workout_hour_local",
            y="relative_performance",
            points="all",
            hover_data=["workout_date_local", "canonical_exercise", "days_since_last_exposure"]
        )
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("Not enough data to display.")

st.markdown("---")
st.subheader("Recent PRs")
prs = df_filtered[df_filtered['is_pr'] == True].sort_values('workout_date_local', ascending=False)
if not prs.empty:
    st.dataframe(prs[['workout_date_local', 'canonical_exercise', 'working_e1rm_lb', 'trailing_90d_max_e1rm_lb', 'reps', 'weight_lb']])
else:
    st.write("No PRs found in the current selection.")
