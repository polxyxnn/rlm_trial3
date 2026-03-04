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

# ====================== PDF SUPPORT ======================
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

st.set_page_config(page_title="PhilSA Rocket Launch Monitoring", page_icon="🚀", layout="wide")

# ====================== SAFE PATHS ======================
if "shape_dir" not in st.session_state:
    st.session_state.shape_dir = "utils/shapefiles"

LOGO_PATH = "utils/logos/PhilSA_v1-01.png"

# ====================== DYNAMIC DROPZONE STORAGE ======================
if "dz_vertices" not in st.session_state:
    st.session_state.dz_vertices = {f"DZ{i}": [""] * 5 for i in range(1, 5)}
    st.session_state.dz_debris   = {f"DZ{i}": [""] * 4 for i in range(1, 5)}

if "pending_notam_fill" not in st.session_state:
    st.session_state.pending_notam_fill = None

# Keep lists in sync
for i in range(1, 5):
    dz = f"DZ{i}"
    for idx in range(len(st.session_state.dz_vertices[dz])):
        key = f"{dz}_vert_{idx}"
        if key in st.session_state:
            st.session_state.dz_vertices[dz][idx] = st.session_state[key]
    for j in range(4):
        key = f"{dz}_deb_{j}"
        if key in st.session_state:
            st.session_state.dz_debris[dz][j] = st.session_state[key]

# ====================== UTILITY FUNCTIONS ======================
def parse_coordinates(coord_str):
    if not coord_str or not isinstance(coord_str, str):
        return None, None
    s = coord_str.strip().upper().replace(",", " ")
    m = re.search(r'(\d{6})(?:\.\d+)?\s*([NS])\s+(\d{7})(?:\.\d+)?\s*([EW])', s)
    if m:
        lat_raw, lat_h, lon_raw, lon_h = m.groups()
        lat = int(lat_raw[:2]) + int(lat_raw[2:4])/60 + int(lat_raw[4:6])/3600
        lon = int(lon_raw[:3]) + int(lon_raw[3:5])/60 + int(lon_raw[5:7])/3600
        if lat_h == 'S': lat = -lat
        if lon_h == 'W': lon = -lon
        return round(lat, 6), round(lon, 6)
    m = re.match(r'^([NS])(\d{2})(\d{2})\s*([EW])(\d{3})(\d{2})$', s.replace(" ", ""))
    if m:
        lat_h, lat_d, lat_m, lon_h, lon_d, lon_m = m.groups()
        lat = int(lat_d) + int(lat_m)/60
        lon = int(lon_d) + int(lon_m)/60
        if lat_h == 'S': lat = -lat
        if lon_h == 'W': lon = -lon
        return round(lat, 6), round(lon, 6)
    dms = re.findall(r'(\d+)[°\s]+(\d+)[\'\s]+(\d+)[\"\s]*([NS])', s)
    dms_lon = re.findall(r'(\d+)[°\s]+(\d+)[\'\s]+(\d+)[\"\s]*([EW])', s)
    if dms and dms_lon:
        lat_d, lat_m, lat_s, lat_h = dms[0]
        lon_d, lon_m, lon_s, lon_h = dms_lon[0]
        lat = int(lat_d) + int(lat_m)/60 + int(lat_s)/3600
        lon = int(lon_d) + int(lon_m)/60 + int(lon_s)/3600
        if lat_h == 'S': lat = -lat
        if lon_h == 'W': lon = -lon
        return round(lat, 6), round(lon, 6)
    lat_match = re.search(r'([-+]?\d+(?:\.\d+)?)\s*([NS])', s)
    lon_match = re.search(r'([-+]?\d+(?:\.\d+)?)\s*([EW])', s)
    if lat_match and lon_match:
        lat = float(lat_match.group(1))
        lon = float(lon_match.group(1))
        if lat_match.group(2) == 'S': lat = -abs(lat)
        if lon_match.group(2) == 'W': lon = -abs(lon)
        return round(lat, 6), round(lon, 6)
    m = re.match(r'\s*([-+]?\d+(?:\.\d+)?)\s*[, \s]\s*([-+]?\d+(?:\.\d+)?)', s)
    if m:
        return round(float(m.group(1)), 6), round(float(m.group(2)), 6)
    return None, None

def convert_to_compact(raw_str):
    if not raw_str or str(raw_str).strip() == "":
        return ""
    s = str(raw_str).upper().replace(" ", "").strip()
    m = re.match(r'^([NS])(\d{2})(\d{2})([EW])(\d{3})(\d{2})$', s)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}{m.group(5)}{m.group(6)}"
    m2 = re.search(r'([NS])\s*(\d{2})(\d{2})\s*([EW])\s*(\d{3})(\d{2})', s)
    if m2:
        return f"{m2.group(1)}{m2.group(2)}{m2.group(3)}{m2.group(4)}{m2.group(5)}{m2.group(6)}"
    latlon = parse_coordinates(raw_str)
    if not latlon or latlon[0] is None:
        return ""
    lat_dd, lon_dd = latlon
    def dd_to_compact(dd, is_lat=True):
        hemi = 'N' if (is_lat and dd >= 0) else 'S' if is_lat else 'E' if dd >= 0 else 'W'
        d = abs(dd)
        deg = int(d)
        minutes_full = (d - deg) * 60
        minute = int(minutes_full)
        sec = (minutes_full - minute) * 60
        if sec >= 30:
            minute += 1
            if minute == 60:
                minute = 0
                deg += 1
        return f"{hemi}{deg:02d}{minute:02d}" if is_lat else f"{hemi}{deg:03d}{minute:02d}"
    return dd_to_compact(lat_dd, True) + dd_to_compact(lon_dd, False)

def utc_window_to_phst(window_utc: str) -> str:
    try:
        s = window_utc.upper().replace("UTC", "").strip()
        start_str, end_str = [x.strip() for x in s.split("-")]
        start_utc = datetime.strptime(start_str, "%H%M")
        end_utc = datetime.strptime(end_str, "%H%M")
        if end_utc <= start_utc: end_utc += timedelta(days=1)
        start_ph = start_utc + timedelta(hours=8)
        end_ph = end_utc + timedelta(hours=8)
        return f"{start_ph.strftime('%I:%M %p').lstrip('0')} - {end_ph.strftime('%I:%M %p').lstrip('0')}"
    except:
        return window_utc

def validate_window_format(hhmm):
    try:
        s = str(hhmm).strip()
        if not re.match(r'^\d{3,4}$', s): return False
        return 0 <= int(s) <= 2359
    except:
        return False

def format_window(start, end):
    if not validate_window_format(start) or not validate_window_format(end):
        return None
    return f"{str(start).zfill(4)}-{str(end).zfill(4)} UTC"

# ====================== NOTAM PDF PARSER ======================
def extract_notam_data(uploaded_file):
    if PdfReader is None:
        st.error("❌ `pypdf` is not installed. Run: `pip install pypdf`")
        return [], {}
    try:
        reader = PdfReader(uploaded_file)
        text = "".join(page.extract_text() or "" for page in reader.pages)
        text_upper = text.upper()

        coord_pattern = r'(\d{6}[NS])\s*(\d{7}[EW])'
        matches = re.findall(coord_pattern, text_upper)
        coord_strings = [f"{lat} {lon}" for lat, lon in matches]
        if len(coord_strings) > 3 and coord_strings[0] == coord_strings[-1]:
            coord_strings = coord_strings[:-1]

        # d_match = re.search(r'D\)\s*(\d{4})-(\d{4})', text_upper)
        # start_time = d_match.group(1) if d_match else ""
        # end_time = d_match.group(2) if d_match else ""
        # if not start_time:
        #     b_match = re.search(r'B\)\s*\d{6}(\d{4})', text_upper)
        #     if b_match:
        #         start_time = b_match.group(1)

        # === TIME PARSING (kept for compatibility) ===
        d_matches = re.findall(r'D\)\s*(\d{4})-(\d{4})', text_upper)
        start_time = d_matches[0][0] if d_matches else ""
        end_time   = d_matches[0][1] if d_matches else ""
        if not start_time:
            b_matches = re.findall(r'B\)\s*\d{6}(\d{4})', text_upper)
            if b_matches:
                start_time = b_matches[0]

        # === NEW: Specific fields for multi-PDF window as requested ===
        # Start time from B) line
        b_matches = re.findall(r'B\)\s*\d{6}(\d{4})', text_upper)
        start_from_b = b_matches[0] if b_matches else ""

        # End time from D) line
        d_matches_end = re.findall(r'D\)\s*(\d{4})-(\d{4})', text_upper)
        end_from_d = d_matches_end[0][1] if d_matches_end else ""

        #country = "People's Republic of China" if re.search(r'CHINA|PRC', text_upper) else ""
        #mission_match = re.search(r'SPECIAL OPS \((.*?)\)', text, re.IGNORECASE)
        #mission = mission_match.group(1).strip() if mission_match else "AEROSPACE FLT ACT"

        return coord_strings, {
            "start_time": start_time,
            "end_time": end_time,
            "start_from_b": start_from_b,   # Used for 1st PDF
            "end_from_d": end_from_d       # Used for last PDF
        }
    
    except Exception as e:
        st.error(f"Error parsing {uploaded_file.name}: {str(e)}")
        return [], {}

# ====================== CACHED DATA & MAP ======================
@st.cache_data
def load_mapping_data(shape_dir: str):
    data = {}
    csv_path = os.path.join(shape_dir, "Launch_Centers_Coords.csv")
    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path)
        data["launch_sites"] = {str(row["Place"]).strip(): (float(row["Lat"]), float(row["Lon"])) for _, row in df.iterrows()}
    else:
        data["launch_sites"] = {}

    key_path = os.path.join(shape_dir, "Updated_Key_Locations.shp")
    key_locations = {}
    if os.path.exists(key_path):
        try:
            key_gdf = gpd.read_file(key_path)
            possible = ["Place", "PlaceName", "Name", "NAME", "name", "Label", "Location", "SiteName"]
            name_field = next((f for f in possible if f in key_gdf.columns), None)
            if not name_field:
                non_geom = [c for c in key_gdf.columns if c.lower() not in ("geometry", "geom")]
                name_field = non_geom[0] if non_geom else None
            for idx, row in key_gdf.iterrows():
                if row.geometry and row.geometry.geom_type == "Point":
                    label = str(row[name_field]).strip() if name_field else f"Key_{idx}"
                    key_locations[label] = (row.geometry.y, row.geometry.x)
        except:
            pass
    data["key_locations"] = key_locations

    data["manila_fir"] = os.path.join(shape_dir, "Manila_FIR_boundary.shp")
    data["baseline"] = os.path.join(shape_dir, "PH_Baseline.shp")
    data["eez"] = os.path.join(shape_dir, "eez.shp")
    return data

def create_folium_map(launch_site_value, dropzones, shape_dir):
    loaded = load_mapping_data(shape_dir)
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

    all_coords = [p for _, pts in polygons for p in pts] + [p for _, pts in debris_points for p in pts]
    avg_lat = sum(p[0] for p in all_coords) / len(all_coords)
    avg_lon = sum(p[1] for p in all_coords) / len(all_coords)

    m = folium.Map(location=[avg_lat, avg_lon], zoom_start=7)

    if launch_site_value in loaded["launch_sites"]:
        lat, lon = loaded["launch_sites"][launch_site_value]
        folium.Marker([lat, lon], popup=f"Launch Site: {launch_site_value}",
                      icon=folium.Icon(color="green", icon="rocket", prefix="fa")).add_to(m)

    for name, (lat, lon) in loaded["key_locations"].items():
        folium.Marker([lat, lon], popup=name,
                      icon=folium.Icon(color="red", icon="location-crosshairs", prefix="fa")).add_to(m)

    for path, color, weight, fill in [
        (loaded["manila_fir"], "darkblue", 1.5, False),
        (loaded["baseline"], "gold", 1.5, False),
        (loaded["eez"], "blue", 1.5, True)
    ]:
        if os.path.exists(path):
            try:
                gdf = gpd.read_file(path)
                for _, row in gdf.iterrows():
                    geom = row.geometry
                    if not geom: continue
                    polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms) if hasattr(geom, "geoms") else []
                    for poly in polys:
                        x, y = poly.exterior.xy
                        if fill:
                            folium.Polygon(list(zip(y, x)), color=color, weight=weight, fill=True, fill_opacity=0.05).add_to(m)
                        else:
                            folium.PolyLine(list(zip(y, x)), color=color, weight=weight).add_to(m)
            except:
                pass

    colors = ['darkgreen', 'green', 'darkblue', 'purple']
    for idx, (dzid, pts) in enumerate(polygons):
        folium.Polygon(pts, popup=f"{dzid}", color=colors[idx % 4], fill=True, fill_opacity=0.3).add_to(m)
        for vi, (lat, lon) in enumerate(pts):
            folium.CircleMarker([lat, lon], radius=4, fill=True, popup=f"{dzid} V{vi+1}").add_to(m)

    for _, (dzid, dpts) in enumerate(debris_points):
        for di, (lat, lon) in enumerate(dpts):
            folium.Marker([lat, lon], popup=f"{dzid} debris {di+1}",
                          icon=folium.Icon(color="black", icon="trash", prefix="fa")).add_to(m)

    def polygon_centroid(pts):
        poly = Polygon([(lon, lat) for lat, lon in pts])
        c = poly.centroid
        return (c.y, c.x)

    def bearing_between(p1, p2):
        lat1, lon1 = map(math.radians, p1)
        lat2, lon2 = map(math.radians, p2)
        dlon = lon2 - lon1
        x = math.sin(dlon) * math.cos(lat2)
        y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
        return (math.degrees(math.atan2(x, y)) + 360) % 360

    def destination_point(lat, lon, bearing_deg, distance_km):
        R = 6371.0
        bearing = math.radians(bearing_deg)
        lat1 = math.radians(lat)
        lon1 = math.radians(lon)
        d_div_r = distance_km / R
        lat2 = math.asin(math.sin(lat1)*math.cos(d_div_r) + math.cos(lat1)*math.sin(d_div_r)*math.cos(bearing))
        lon2 = lon1 + math.atan2(math.sin(bearing)*math.sin(d_div_r)*math.cos(lat1),
                                 math.cos(d_div_r) - math.sin(lat1)*math.sin(lat2))
        return (math.degrees(lat2), math.degrees(lon2))

    centroids = [polygon_centroid(pts) for _, pts in polygons if len(pts) >= 3]
    if launch_site_value in loaded["launch_sites"] and centroids:
        route = [loaded["launch_sites"][launch_site_value]]
        route.extend(centroids)
        if len(centroids) >= 2:
            last_bearing = bearing_between(centroids[-2], centroids[-1])
        else:
            last_bearing = bearing_between(route[0], centroids[-1])
        ext = destination_point(centroids[-1][0], centroids[-1][1], last_bearing, 1852)
        route.append(ext)
        folium.PolyLine(route, color="black", weight=2, dash_array="5,10", popup="Rocket Ground Track").add_to(m)

    # legend = """<div style="position:fixed;bottom:30px;left:30px;z-index:9999;background:white;padding:10px;border:2px solid grey;font-size:13px;">
    # <b>Legend</b><br>
    # <i class="fa fa-rocket" style="color:green"></i> Launch Site<br>
    # <i class="fa fa-location-crosshairs" style="color:red"></i> Key Locations<br>
    # <i class="fa fa-trash" style="color:black"></i> Debris<br>
    # <span style="color:darkgreen;">■</span> Dropzone
    # </div>"""
    legend = """
    <div style="
        position: fixed; 
        bottom: 50px; left: 50px; width: 250px; height: 250px; 
        z-index:9999; font-size:14px;
        background-color:white;
        border:2px solid grey;
        padding: 10px;
        ">
    <b>Legend</b><br>
    <i class="fa fa-rocket fa-2x" style="color:green"></i> Launch Site<br>
    <i class="fa fa-location-crosshairs fa-2x" style="color:red"></i> Key Locations<br>
    <i class="fa fa-trash fa-2x" style="color:black"></i> Debris Point<br>
    <span style="color:darkblue;">&#9632;</span> Manila FIR<br>
    <span style="color:gold;">&#9632;</span> PH Baseline<br>
    <span style="color:blue;">&#9632;</span> PH EEZ<br>
    <span style="color:green;">&#9632;</span> Dropzone<br>
    <span style="display:inline-block; width:50px; border-bottom: 2px dashed black;"></span> Rocket Ground Track
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))
    return m

# ====================== MAIN APP ======================
st.title("🚀 Philippine Space Agency – Rocket Launch Monitoring")

with st.sidebar:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=180)
    else:
        st.warning("⚠️ Logo not found")

    st.header("📁 Paths")
    new_dir = st.text_input("Shapefiles Folder", value=st.session_state.shape_dir, key="shape_dir_input")
    if new_dir != st.session_state.shape_dir:
        st.session_state.shape_dir = new_dir

launch_sites = ["Select...", "Hainan International Commercial Launch Center", "Jiuquan Satellite Launch Center",
                "Wenchang Space Launch Site", "Xichang Satellite Launch Center",
                "Naro Space Center", "Sohae Satellite Launching Station", "Unchinoura Space Center", "Other"]
countries = ["Select...", "People's Republic of China", "Japan",
             "Democratic People's Republic of Korea", "Republic of Korea"]

# ====================== APPLY PENDING NOTAM FILL (BEFORE FORM WIDGETS) ======================
if st.session_state.get("pending_notam_fill"):
    fill = st.session_state.pending_notam_fill
    if fill.get("mission"):
        st.session_state.mission = fill["mission"]
    if fill.get("country") and fill["country"] in countries:
        st.session_state.launch_country = fill["country"]
    if fill.get("start_time"):
        st.session_state.start_time = fill["start_time"]
    if fill.get("end_time"):
        st.session_state.end_time = fill["end_time"]
    st.session_state.pending_notam_fill = None

# ====================== FORM ======================
with st.form("rocket_launch_form"):
    st.subheader("🛰 Launch Information")
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1: st.text_input("Mission Name", placeholder="SLV-XX", key="mission")
    with col2: st.selectbox("Launch Site", launch_sites, key="launch_site")
    with col3: st.selectbox("Country", countries, key="launch_country")

    col4, col5, col6 = st.columns([1, 1, 1])
    with col4: st.date_input("Launch Date", value=date.today(), key="launch_date")
    with col5: st.text_input("Window Start (HHMM)", placeholder="0745", key="start_time")
    with col6: st.text_input("Window End (HHMM)", placeholder="0810", key="end_time")

    submitted = st.form_submit_button("🚀 Submit – Preview Map & Generate Files", 
                                      type="primary", use_container_width=True)

# ====================== NOTAM PDF IMPORT ======================
st.subheader("📄 Import Dropzones from FAA NOTAM PDF(s)")
st.caption("**Each PDF = one dropzone** (1st PDF → DZ1, 2nd → DZ2, etc.)")

uploaded_pdfs = st.file_uploader(
    "Upload NOTAM PDF file(s)",
    type="pdf",
    accept_multiple_files=True,
    key="notam_uploader"
)

if uploaded_pdfs:
    st.info(f"📌 {len(uploaded_pdfs)} PDF(s) selected")
    if st.button("🔄 Parse NOTAMs & Auto-fill Dropzones", type="primary", use_container_width=True):
        processed = 0
        first_meta = {}
        for pdf_file in uploaded_pdfs:
            if processed >= 4:
                st.warning("Only first 4 PDFs processed (max 4 dropzones)")
                break
            coord_strings, meta = extract_notam_data(pdf_file)
            if coord_strings and len(coord_strings) >= 3:
                dz_key = f"DZ{processed + 1}"
                st.session_state.dz_vertices[dz_key] = coord_strings[:]
                for idx, val in enumerate(coord_strings):
                    st.session_state[f"{dz_key}_vert_{idx}"] = val
                for idx in range(len(coord_strings), 10):
                    key = f"{dz_key}_vert_{idx}"
                    if key in st.session_state:
                        del st.session_state[key]
                
                if processed == 0:
                    first_meta = meta.copy()
                processed += 1
        
        if processed > 0:
            st.session_state.pending_notam_fill = first_meta
            st.success(f"✅ Auto-filled {processed} dropzone(s) from NOTAM PDF(s)!")
            st.rerun()

# ====================== DYNAMIC DROPZONES ======================
st.subheader("🌍 Dropzones DZ1–DZ4")

for i in range(1, 5):
    dz = f"DZ{i}"
    with st.expander(f"**Dropzone {i}**", expanded=(i == 1)):
        st.caption("**Vertices** (min 3 recommended)")
        current_verts = st.session_state.dz_vertices[dz]
        for idx in range(len(current_verts)):
            st.text_input(
                label=f"Vertex {idx+1}",
                value=current_verts[idx],
                key=f"{dz}_vert_{idx}",
                placeholder="193600N 1183100E"
            )
        col_add, col_rem, _ = st.columns([1, 1, 4])
        with col_add:
            if st.button("➕ Add Vertex", key=f"add_v_{dz}", use_container_width=True):
                if len(current_verts) < 10:
                    st.session_state.dz_vertices[dz].append("")
                    st.rerun()
                else:
                    st.warning("Maximum 10 vertices")
        with col_rem:
            if len(current_verts) > 3 and st.button("➖ Remove Last", key=f"rem_v_{dz}", use_container_width=True):
                st.session_state.dz_vertices[dz].pop()
                st.rerun()

        st.caption("**Debris Points** (fixed 4)")
        for j in range(4):
            st.text_input(
                label=f"Debris {j+1}",
                value=st.session_state.dz_debris[dz][j],
                key=f"{dz}_deb_{j}",
                placeholder="14.5N 120.5E"
            )

# ====================== MAP & FILE GENERATION ======================
if "map_object" not in st.session_state:
    st.session_state.map_object = None

if submitted:
    window_utc = format_window(st.session_state.start_time, st.session_state.end_time)
    if window_utc is None:
        st.error("❌ Invalid time window. Use HHMM format (e.g. 0745)")
        st.stop()

    dropzones = {}
    for i in range(1, 5):
        dz_key = f"DZ{i}"
        dropzones[dz_key] = {
            "vertices": st.session_state.dz_vertices[dz_key].copy(),
            "debris": st.session_state.dz_debris[dz_key].copy()
        }

    for dz_id, dz in dropzones.items():
        valid_verts = [p.strip() for p in dz["vertices"] if p.strip()]
        if len(valid_verts) < 3 and any(p.strip() for p in dz["vertices"]):
            st.error(f"❌ {dz_id} needs at least 3 valid vertices")
            st.stop()

    with st.spinner("Generating map..."):
        st.session_state.map_object = create_folium_map(
            st.session_state.launch_site,
            dropzones,
            st.session_state.shape_dir
        )

    window_phst = utc_window_to_phst(window_utc)
    
    dz_compact = []
    for i in range(1, 5):
        verts = dropzones[f"DZ{i}"]["vertices"]
        compact_verts = [convert_to_compact(c) for c in verts]
        compact_verts += [""] * (8 - len(compact_verts))
        dz_compact.append(compact_verts[:8])

    deb_compact = [[convert_to_compact(c) for c in dropzones[f"DZ{i}"]["debris"]] for i in range(1,5)]

    date_str = st.session_state.launch_date.strftime("%m%d%y")
    formatted_date = st.session_state.launch_date.strftime("%d %B %Y")

    row_base = {
        "ROCKET NAME": st.session_state.mission,
        "LAUNCHING STATE": st.session_state.launch_country,
        "LAUNCH CENTER": st.session_state.launch_site,
        "LAUNCH CENTER LOCATION": "",
        "START TO END TIME": window_phst,
        "DATE RECEIVED FROM CAAP": "",
    }
    for i in range(4):
        for j in range(8):
            row_base[f"DZ{i+1} P{j+1}"] = dz_compact[i][j]
    for i in range(4):
        for j in range(4):
            row_base[f"DEB{i+1} P{j+1}"] = deb_compact[i][j]

    info_df = pd.DataFrame([{"LAUNCH DATE": f"'{formatted_date}", **row_base}])
    info_xlsx_df = pd.DataFrame([{"LAUNCH DATE": f'=TEXT(DATE({st.session_state.launch_date.year},{st.session_state.launch_date.month},{st.session_state.launch_date.day}),"dd MMMM yyyy")', **row_base}])

    vertices_rows, debris_rows = [], []
    for i in range(1, 5):
        dz = dropzones[f"DZ{i}"]
        for vi, c in enumerate(dz["vertices"]):
            lat, lon = parse_coordinates(c)
            if lat is not None:
                vertices_rows.append({"DZ_ID": f"DZ{i}", "VERTEX_ID": vi+1, "LAT": lat, "LON": lon})
        for di, c in enumerate(dz["debris"]):
            lat, lon = parse_coordinates(c)
            if lat is not None:
                debris_rows.append({"DZ_ID": f"DZ{i}", "DB_ID": di+1, "LAT": lat, "LON": lon})

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"Info_{date_str}.csv", info_df.to_csv(index=False).encode())
        xlsx_buf = io.BytesIO()
        with pd.ExcelWriter(xlsx_buf, engine="openpyxl") as writer:
            info_xlsx_df.to_excel(writer, index=False)
        zf.writestr(f"Info_{date_str}.xlsx", xlsx_buf.getvalue())
        zf.writestr(f"DZ_{date_str}.csv", pd.DataFrame(vertices_rows).to_csv(index=False).encode())
        zf.writestr(f"Deb_{date_str}.csv", pd.DataFrame(debris_rows).to_csv(index=False).encode())

    zip_buffer.seek(0)

    st.success(f"✅ Files ready for {formatted_date}!")
    st.download_button("📦 Download All Files (ZIP)", data=zip_buffer,
                       file_name=f"PhilSA_Launch_{date_str}.zip", mime="application/zip", use_container_width=True)

if st.session_state.map_object is not None:
    st.subheader("📍 Live Preview Map")
    st_folium(st.session_state.map_object, width=1400, height=750, returned_objects=[], key="launch_map")

    col_clear, _ = st.columns([1, 3])
    with col_clear:
        if st.button("🗑️ Clear Map", use_container_width=True):
            st.session_state.map_object = None
            st.rerun()

st.caption("Philippine Space Agency • Streamlit Cloud Ready")