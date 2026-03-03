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
# SESSION STATE
# =========================================================
if "shape_dir" not in st.session_state:
    st.session_state.shape_dir = "utils/shapefiles"

if "map_object" not in st.session_state:
    st.session_state.map_object = None

if "zip_file" not in st.session_state:
    st.session_state.zip_file = None

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
def load_mapping_data(shape_dir):
    data = {}

    csv_path = os.path.join(shape_dir, "Launch_Centers_Coords.csv")
    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path)
        data["launch_sites"] = {
            str(row["Place"]).strip(): (float(row["Lat"]), float(row["Lon"]))
            for _, row in df.iterrows()
        }
    else:
        data["launch_sites"] = {}

    data["manila_fir"] = load_shapefile(os.path.join(shape_dir, "Manila_FIR_boundary.shp"))
    data["baseline"] = load_shapefile(os.path.join(shape_dir, "PH_Baseline.shp"))
    data["eez"] = load_shapefile(os.path.join(shape_dir, "eez.shp"))

    return data

# =========================================================
# ORIGINAL FULL COORD PARSER (RESTORED)
# =========================================================
def parse_coordinates(coord_str):
    if not coord_str or not isinstance(coord_str, str):
        return None, None

    s = coord_str.strip().upper().replace(",", " ")

    # Decimal degrees
    m = re.match(r'\s*([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)', s)
    if m:
        return round(float(m.group(1)), 6), round(float(m.group(2)), 6)

    # Compact NDDMMEDDMM
    m = re.match(r'^([NS])(\d{2})(\d{2})([EW])(\d{3})(\d{2})$', s.replace(" ", ""))
    if m:
        lat_h, lat_d, lat_m, lon_h, lon_d, lon_m = m.groups()
        lat = int(lat_d) + int(lat_m)/60
        lon = int(lon_d) + int(lon_m)/60
        if lat_h == "S": lat = -lat
        if lon_h == "W": lon = -lon
        return round(lat,6), round(lon,6)

    return None, None

def convert_to_compact(raw_str):
    latlon = parse_coordinates(raw_str)
    if latlon[0] is None:
        return ""

    lat_dd, lon_dd = latlon

    def dd_to_compact(dd, is_lat=True):
        hemi = 'N' if (is_lat and dd >= 0) else 'S' if is_lat else 'E' if dd >= 0 else 'W'
        d = abs(dd)
        deg = int(d)
        minutes_full = (d - deg) * 60
        minute = int(minutes_full)
        return f"{hemi}{deg:02d}{minute:02d}" if is_lat else f"{hemi}{deg:03d}{minute:02d}"

    return dd_to_compact(lat_dd, True) + dd_to_compact(lon_dd, False)

# =========================================================
# MAP GENERATOR (FULL ORIGINAL FEATURES)
# =========================================================
def create_folium_map(launch_site_value, dropzones):

    loaded = load_mapping_data(st.session_state.shape_dir)
    polygons, debris_points = [], []

    for dz_id, dz in dropzones.items():
        pts = [parse_coordinates(c) for c in dz["vertices"]]
        pts = [(lat, lon) for lat, lon in pts if lat is not None]
        if len(pts) >= 3:
            polygons.append((dz_id, pts))

        dpts = [parse_coordinates(c) for c in dz["debris"]]
        dpts = [(lat, lon) for lat, lon in dpts if lat is not None]
        if dpts:
            debris_points.append((dz_id, dpts))

    if not polygons and not debris_points:
        return folium.Map(location=[14.5, 120.5], zoom_start=6)

    all_coords = [p for _, pts in polygons for p in pts] + \
                 [p for _, pts in debris_points for p in pts]

    avg_lat = sum(p[0] for p in all_coords) / len(all_coords)
    avg_lon = sum(p[1] for p in all_coords) / len(all_coords)

    m = folium.Map(location=[avg_lat, avg_lon], zoom_start=7, tiles="CartoDB positron")

    # Launch site marker
    if launch_site_value in loaded["launch_sites"]:
        lat, lon = loaded["launch_sites"][launch_site_value]
        folium.Marker(
            [lat, lon],
            popup=f"Launch Site: {launch_site_value}",
            icon=folium.Icon(color="green", icon="rocket", prefix="fa")
        ).add_to(m)

    # Overlay shapefiles
    for gdf in [loaded["manila_fir"], loaded["baseline"], loaded["eez"]]:
        if gdf is not None:
            for _, row in gdf.iterrows():
                geom = row.geometry
                if geom is None: continue
                if geom.geom_type == "Polygon":
                    x, y = geom.exterior.xy
                    folium.PolyLine(list(zip(y, x)), color="blue", weight=1).add_to(m)

    # Dropzones
    colors = ['darkgreen', 'green', 'purple', 'darkblue']
    for idx, (dzid, pts) in enumerate(polygons):
        folium.Polygon(
            pts,
            popup=dzid,
            color=colors[idx % 4],
            fill=True,
            fill_opacity=0.3
        ).add_to(m)

    # Debris
    for dzid, dpts in debris_points:
        for di, (lat, lon) in enumerate(dpts):
            folium.Marker(
                [lat, lon],
                popup=f"{dzid} Debris {di+1}",
                icon=folium.Icon(color="black", icon="trash", prefix="fa")
            ).add_to(m)

    return m

# =========================================================
# SIDEBAR FORM (NO AUTO RERUN)
# =========================================================
with st.sidebar:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=180)

    with st.form("path_form"):
        new_dir = st.text_input("Shapefiles Folder", value=st.session_state.shape_dir)
        update_path = st.form_submit_button("Update")

    if update_path:
        st.session_state.shape_dir = new_dir
        st.success("Path updated")

# =========================================================
# MAIN FORM (FULL RESTORED INPUTS)
# =========================================================
st.title("🚀 Philippine Space Agency – Rocket Launch Monitoring")

with st.form("rocket_launch_form"):

    st.subheader("Launch Information")

    mission = st.text_input("Mission Name")
    launch_site = st.text_input("Launch Site")
    launch_country = st.text_input("Country")

    launch_date = st.date_input("Launch Date", value=date.today())
    start_time = st.text_input("Window Start (HHMM)", "0745")
    end_time = st.text_input("Window End (HHMM)", "0810")

    st.subheader("Dropzones DZ1–DZ4")

    dropzones = {}

    for i in range(1, 5):
        with st.expander(f"Dropzone {i}", expanded=True):
            vertices = [st.text_input(f"DZ{i} V{j+1}", key=f"v_{i}_{j}") for j in range(8)]
            debris = [st.text_input(f"DZ{i} D{j+1}", key=f"d_{i}_{j}") for j in range(4)]
        dropzones[f"DZ{i}"] = {"vertices": vertices, "debris": debris}

    submitted = st.form_submit_button("🚀 Generate Map & Files", use_container_width=True)

# =========================================================
# PROCESS ONLY WHEN SUBMITTED
# =========================================================
if submitted:

    with st.spinner("Generating map..."):
        st.session_state.map_object = create_folium_map(launch_site, dropzones)

    # ZIP creation restored
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        df = pd.DataFrame([{
            "Mission": mission,
            "Country": launch_country,
            "Launch Site": launch_site,
            "Date": launch_date
        }])
        zf.writestr("Launch_Info.csv", df.to_csv(index=False).encode())

    zip_buffer.seek(0)
    st.session_state.zip_file = zip_buffer

# =========================================================
# MAP DISPLAY (NO RERUN ON ZOOM)
# =========================================================
if st.session_state.map_object:
    st.subheader("📍 Preview Map")
    st_folium(
        st.session_state.map_object,
        width=1400,
        height=750,
        returned_objects=[]
    )

    if st.button("🗑️ Clear Map"):
        st.session_state.map_object = None
        st.session_state.zip_file = None
        st.rerun()

# =========================================================
# DOWNLOAD
# =========================================================
if st.session_state.zip_file:
    st.download_button(
        "📦 Download ZIP",
        data=st.session_state.zip_file,
        file_name="PhilSA_Launch.zip",
        mime="application/zip",
        use_container_width=True
    )

st.caption("Philippine Space Agency • Stable Build")