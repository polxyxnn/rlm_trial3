import streamlit as st
import os
import re
import math
import io
import zipfile
import pandas as pd
import geopandas as gpd
import folium
from shapely.geometry import Polygon
from datetime import datetime, timedelta, date
from streamlit_folium import st_folium

st.set_page_config(page_title="PhilSA Rocket Launch Monitoring", page_icon="🚀", layout="wide")

# =========================================================
# INITIAL SESSION STATE
# =========================================================
if "shape_dir" not in st.session_state:
    st.session_state.shape_dir = "utils/shapefiles"

if "map_object" not in st.session_state:
    st.session_state.map_object = None

if "files_ready" not in st.session_state:
    st.session_state.files_ready = None

LOGO_PATH = "utils/logos/PhilSA_v1-01.png"

# =========================================================
# CACHED LOADERS
# =========================================================
@st.cache_data
def load_shapefile(path):
    if os.path.exists(path):
        return gpd.read_file(path)
    return None

@st.cache_data
def load_launch_sites(shape_dir):
    csv_path = os.path.join(shape_dir, "Launch_Centers_Coords.csv")
    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path)
        return {str(row["Place"]).strip(): (float(row["Lat"]), float(row["Lon"])) for _, row in df.iterrows()}
    return {}

# =========================================================
# UTILITIES
# =========================================================
def parse_coordinates(coord_str):
    if not coord_str:
        return None, None
    try:
        lat, lon = map(float, coord_str.replace(",", " ").split())
        return lat, lon
    except:
        return None, None

def validate_window_format(hhmm):
    if not re.match(r'^\d{3,4}$', str(hhmm)):
        return False
    return 0 <= int(hhmm) <= 2359

def format_window(start, end):
    if not validate_window_format(start) or not validate_window_format(end):
        return None
    return f"{str(start).zfill(4)}-{str(end).zfill(4)} UTC"

# =========================================================
# MAP GENERATOR
# =========================================================
def create_map(launch_site, dropzones):

    launch_sites = load_launch_sites(st.session_state.shape_dir)

    all_coords = []
    polygons = []

    for dz_id, dz in dropzones.items():
        pts = [parse_coordinates(c) for c in dz["vertices"]]
        pts = [(lat, lon) for lat, lon in pts if lat is not None]
        if len(pts) >= 3:
            polygons.append((dz_id, pts))
            all_coords.extend(pts)

    if not all_coords:
        return folium.Map(location=[14.5, 120.5], zoom_start=6)

    avg_lat = sum(p[0] for p in all_coords) / len(all_coords)
    avg_lon = sum(p[1] for p in all_coords) / len(all_coords)

    m = folium.Map(location=[avg_lat, avg_lon], zoom_start=7, tiles="CartoDB positron")

    if launch_site in launch_sites:
        lat, lon = launch_sites[launch_site]
        folium.Marker([lat, lon], popup="Launch Site",
                      icon=folium.Icon(color="green", icon="rocket", prefix="fa")).add_to(m)

    colors = ["darkgreen", "green", "purple", "darkblue"]

    for idx, (dzid, pts) in enumerate(polygons):
        folium.Polygon(
            pts,
            popup=dzid,
            color=colors[idx % 4],
            fill=True,
            fill_opacity=0.3
        ).add_to(m)

    return m

# =========================================================
# SIDEBAR (NO AUTO-RERUN)
# =========================================================
with st.sidebar:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=180)

    st.header("📁 Shapefile Path")

    with st.form("path_form"):
        new_dir = st.text_input("Folder", value=st.session_state.shape_dir)
        update = st.form_submit_button("Update Path")

    if update:
        st.session_state.shape_dir = new_dir
        st.success("Path Updated")

# =========================================================
# MAIN FORM
# =========================================================
st.title("🚀 Philippine Space Agency – Rocket Launch Monitoring")

launch_sites_list = [
    "Select...",
    "Hainan International Commercial Launch Center",
    "Jiuquan Satellite Launch Center",
    "Wenchang Space Launch Site",
    "Xichang Satellite Launch Center",
    "Naro Space Center",
    "Sohae Satellite Launching Station",
    "Unchinoura Space Center"
]

countries = [
    "Select...",
    "People's Republic of China",
    "Japan",
    "Democratic People's Republic of Korea",
    "Republic of Korea"
]

with st.form("launch_form"):
    st.subheader("Launch Information")

    col1, col2, col3 = st.columns(3)
    with col1:
        mission = st.text_input("Mission Name")
    with col2:
        launch_site = st.selectbox("Launch Site", launch_sites_list)
    with col3:
        country = st.selectbox("Country", countries)

    col4, col5, col6 = st.columns(3)
    with col4:
        launch_date = st.date_input("Launch Date", value=date.today())
    with col5:
        start_time = st.text_input("Window Start (HHMM)", "0745")
    with col6:
        end_time = st.text_input("Window End (HHMM)", "0810")

    st.subheader("Dropzones")

    dropzones = {}
    for i in range(1, 5):
        with st.expander(f"DZ{i}", expanded=False):
            vertices = [st.text_input(f"DZ{i} V{j+1}", key=f"v_{i}_{j}") for j in range(4)]
        dropzones[f"DZ{i}"] = {"vertices": vertices}

    submitted = st.form_submit_button("🚀 Generate Map & Files", use_container_width=True)

# =========================================================
# PROCESS ON SUBMIT ONLY
# =========================================================
if submitted:

    window = format_window(start_time, end_time)
    if window is None:
        st.error("Invalid time window format.")
        st.stop()

    with st.spinner("Generating map..."):
        st.session_state.map_object = create_map(launch_site, dropzones)

    # Prepare ZIP
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        df = pd.DataFrame([{
            "Mission": mission,
            "Country": country,
            "Launch Site": launch_site,
            "Date": launch_date,
            "Window": window
        }])
        zf.writestr("Launch_Info.csv", df.to_csv(index=False).encode())

    zip_buffer.seek(0)
    st.session_state.files_ready = zip_buffer

# =========================================================
# DISPLAY MAP (NO RERUN ON INTERACTION)
# =========================================================
if st.session_state.map_object is not None:

    st.subheader("📍 Launch Preview Map")

    st_folium(
        st.session_state.map_object,
        width=1400,
        height=700,
        returned_objects=[]
    )

    if st.button("🗑️ Clear Map"):
        st.session_state.map_object = None
        st.session_state.files_ready = None
        st.rerun()

# =========================================================
# DOWNLOAD BUTTON
# =========================================================
if st.session_state.files_ready is not None:
    st.download_button(
        "📦 Download Files (ZIP)",
        data=st.session_state.files_ready,
        file_name="PhilSA_Launch.zip",
        mime="application/zip",
        use_container_width=True
    )

st.caption("Philippine Space Agency • Streamlit Cloud Optimized")