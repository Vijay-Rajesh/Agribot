import ee
import re
import calendar
import datetime
import json
import logging
import math
import os
from pathlib import Path
import requests
from typing import Callable, List, Tuple, Dict, Optional
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak
from reportlab.lib.units import inch
from reportlab.lib.pagesizes import A4
import tempfile
import os
import matplotlib.pyplot as plt
import numpy as np
from services.government import GovernmentSchemes
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

class FarmBotAnalyzer:
    """Main analysis class for FarmBot with all agricultural analysis capabilities"""
    
    @staticmethod
    def parse_user_input(user_input: Optional[str]) -> Dict:
        """Parse user input to extract coordinates, date range, and analysis type with proper null checks"""
        # Initialize default result
        result = {
            "coordinates": None,
            "date_range": None,
            "analysis_type": "full",
            "language": "english",
            "other_instructions": [],
            "special_requests": []
        }

        # Handle null/empty input
        if not user_input or not isinstance(user_input, str):
            return result

        # Ensure string processing
        user_input = user_input.strip()
        if not user_input:
            return result

        # Language detection
        if any(word in user_input.lower() for word in ["urdu", "اردو"]):
            result["language"] = "urdu"
            
        # Coordinate parsing with try-except
        coord_pattern = r'(\d+\.\d+)\s*,\s*(\d+\.\d+)'
        try:
            coords = re.findall(coord_pattern, user_input)
            if coords:
                result["coordinates"] = [(float(lat), float(lon)) for lat, lon in coords]
        except (ValueError, TypeError):
            pass
                    
        # Date range parsing with validation
        date_pattern = r'(?:from|between)\s*(\d{4}-\d{2}-\d{2}|\d{1,2}\s+\w+)\s*(?:to|and)\s*(\d{4}-\d{2}-\d{2}|\d{1,2}\s+\w+)'
        dates = re.search(date_pattern, user_input, re.IGNORECASE)
        if dates:
            try:
                start_date = FarmBotAnalyzer._parse_date_string(dates.group(1))
                end_date = FarmBotAnalyzer._parse_date_string(dates.group(2))
                if start_date and end_date:
                    result["date_range"] = (start_date, end_date)
            except (ValueError, AttributeError):
                pass
                    
        # Analysis type detection with fallback
        analysis_types = {
            "ndvi": "ndvi_only",
            "soil": "soil_moisture",
            "temperature": "temp_only",
            "health": "crop_health",
            "pest": "pest_risk"
        }
        
        try:
            for term, code in analysis_types.items():
                if term in user_input.lower():
                    result["analysis_type"] = code
                    break
        except AttributeError:
            pass  # Maintain default if string operations fail
                    
        return result
        
    @staticmethod
    def _parse_date_string(date_str: str) -> Optional[str]:
        """Helper to parse date strings with enhanced validation"""
        if not date_str or not isinstance(date_str, str):
            return None
            
        try:
            # Handle month-day formats (e.g., "15 June")
            if not any(char.isdigit() for char in date_str[:4]):
                parsed = datetime.datetime.strptime(date_str, "%d %B")
                return parsed.replace(year=datetime.datetime.now().year).strftime("%Y-%m-%d")
            
            # Handle standard YYYY-MM-DD format
            return datetime.datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return None
            
    @staticmethod
    def get_analysis_data(coords: Optional[List[Tuple[float, float]]],
                        date_range: Optional[Tuple[str, str]] = None,
                        analysis_type: str = "full",
                        other_instructions: List[str] = [],
                        place: Optional[str] = None,
                        radius_m: Optional[float] = None,
                        area_hectares: Optional[float] = None,
                        area_acres: Optional[float] = None,
                        progress_callback: Optional[Callable[[Dict], None]] = None) -> Dict:
        """Create monthly NDVI/NDMI maps and mean values for the requested area."""
        if coords:
            lat, lon = coords[0]
        elif place:
            lat, lon, place = FarmBotAnalyzer._geocode_place(place)
        else:
            raise ValueError("Provide coordinates or a place name for satellite analysis.")

        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Coordinates must be valid latitude/longitude values.")

        area_options = [value for value in (radius_m, area_hectares, area_acres) if value is not None]
        if len(area_options) > 1:
            raise ValueError("Specify only one of radius, hectares, or acres.")

        if radius_m is not None:
            if not math.isfinite(radius_m) or radius_m <= 0:
                raise ValueError("Radius must be greater than zero.")
            half_side_m = radius_m
            requested_area_description = f"requested {radius_m:g} m radius"
        elif area_hectares is not None or area_acres is not None:
            area_m2 = area_hectares * 10_000 if area_hectares is not None else area_acres * 4_046.8564224
            if not math.isfinite(area_m2) or area_m2 <= 0:
                raise ValueError("Requested area must be greater than zero.")
            half_side_m = math.sqrt(area_m2) / 2
            requested_area_description = (
                f"{area_hectares:g} hectares"
                if area_hectares is not None
                else f"{area_acres:g} acres"
            )
        else:
            half_side_m = 300
            requested_area_description = "default 300 m radius"

        side_m = half_side_m * 2
        area_m2 = side_m ** 2
        area_hectares_actual = area_m2 / 10_000

        if date_range:
            start = datetime.date.fromisoformat(date_range[0])
            end = datetime.date.fromisoformat(date_range[1])
            if start > end:
                raise ValueError("Analysis start date must not be after the end date.")
            period_start, period_end = start, end
        else:
            period_end = datetime.date.today()
            period_start = FarmBotAnalyzer._shift_months(period_end, -2)

        utm_zone = max(1, min(60, int((lon + 180) // 6) + 1))
        utm_epsg = (32600 if lat >= 0 else 32700) + utm_zone
        projection = ee.Projection(f"EPSG:{utm_epsg}")
        aoi = (
            ee.Geometry.Point(lon, lat)
            .buffer(half_side_m, proj=projection)
            .bounds(proj=projection)
        )
        periods = []
        image_root = Path(__file__).resolve().parents[1] / "generated"
        run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        collection = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(aoi)
            .filterDate(period_start.isoformat(), (period_end + datetime.timedelta(days=1)).isoformat())
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 10))
        )
        timestamps = collection.aggregate_array("system:time_start").getInfo()
        acquisition_dates = sorted({
            datetime.datetime.fromtimestamp(ts / 1000, tz=datetime.timezone.utc).date()
            for ts in timestamps
        })
        pixel_data = {
            "location": {"latitude": lat, "longitude": lon, "place": place},
            "area": {
                "shape": "square",
                "side_m": side_m,
                "area_m2": area_m2,
                "area_hectares": area_hectares_actual,
            },
            "analysis_period": {
                "start_date": period_start.isoformat(),
                "end_date": period_end.isoformat(),
            },
            "processing_note": "Scenes with CLOUDY_PIXEL_PERCENTAGE < 10 are used; multiple scenes on the same date are median-composited.",
            "pixel_value_note": "All valid 10 m Sentinel-2 pixel samples are included for each index on each acquisition date.",
            "dates": [],
        }
        total_dates = len(acquisition_dates)
        logger.info(
            "Satellite analysis started: %s date(s), %s to %s, AOI %s",
            total_dates,
            period_start,
            period_end,
            f"{side_m:g} x {side_m:g} m square",
        )
        for date_index, acquisition_date in enumerate(acquisition_dates, start=1):
            logger.info(
                "Processing satellite date %s (%s/%s)",
                acquisition_date,
                date_index,
                total_dates,
            )
            if progress_callback:
                progress_callback({
                    "date": acquisition_date.isoformat(),
                    "date_index": date_index,
                    "total_dates": total_dates,
                    "status": "started",
                })
            date_end = acquisition_date + datetime.timedelta(days=1)
            daily_collection = collection.filterDate(acquisition_date.isoformat(), date_end.isoformat())
            scene_count = daily_collection.size().getInfo()
            if not scene_count:
                continue
            composite = daily_collection.median().clip(aoi)
            daily_result = {
                "selected_date": acquisition_date.isoformat(),
                "start_date": acquisition_date.isoformat(),
                "end_date": acquisition_date.isoformat(),
                "image_count": scene_count,
                "ndvi": None,
                "ndmi": None,
                "ndvi_image": None,
                "ndmi_image": None,
                "ndvi_pixels": [],
                "ndmi_pixels": [],
            }
            pixel_entry = {"date": acquisition_date.isoformat(), "scene_count": scene_count}
            for index_name, bands, palette in (
                ("ndvi", ["B8", "B4"], ["#FF0000", "#FF7F00", "#FFFF00", "#8DD400", "#38A800"]),
                ("ndmi", ["B8", "B11"], ["#BFE9FF", "#40A0FF", "#0053D0", "#002673"]),
            ):
                band_name = index_name.upper()
                index_image = composite.normalizedDifference(bands).rename(band_name)
                value = index_image.reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=aoi,
                    scale=10,
                    maxPixels=100_000_000,
                    tileScale=4,
                ).get(band_name).getInfo()
                samples = index_image.sample(
                    region=aoi,
                    scale=10,
                    seed=0,
                    geometries=True,
                ).getInfo()
                pixel_values = [
                    {
                        "longitude": feature["geometry"]["coordinates"][0],
                        "latitude": feature["geometry"]["coordinates"][1],
                        band_name: round(feature["properties"][band_name], 3),
                    }
                    for feature in samples.get("features", [])
                    if feature.get("properties", {}).get(band_name) is not None
                ]
                image_dir = image_root / index_name
                image_dir.mkdir(parents=True, exist_ok=True)
                image_path = image_dir / (
                    f"farmbot_{index_name}_point_0_{acquisition_date:%Y%m%d}_{run_id}.png"
                )
                thumb_url = index_image.getThumbURL({
                    "region": aoi,
                    "dimensions": 1024,
                    "bands": band_name,
                    "min": 0,
                    "max": 1,
                    "palette": palette,
                    "format": "png",
                })
                response = requests.get(thumb_url, timeout=60)
                response.raise_for_status()
                image_path.write_bytes(response.content)
                logger.info(
                    "Generated %s map for %s: %s (%s pixel sample(s))",
                    band_name,
                    acquisition_date,
                    image_path,
                    len(pixel_values),
                )
                if progress_callback:
                    progress_callback({
                        "date": acquisition_date.isoformat(),
                        "date_index": date_index,
                        "total_dates": total_dates,
                        "status": f"{band_name}_ready",
                    })
                daily_result[index_name] = value
                daily_result[f"{index_name}_image"] = str(image_path)
                daily_result[f"{index_name}_pixels"] = pixel_values
                pixel_entry[index_name.upper()] = pixel_values
            pixel_data["dates"].append(pixel_entry)
            periods.append(daily_result)

        if not periods:
            raise ValueError("No Sentinel-2 images were found for this location and date range.")
        logger.info(
            "Satellite analysis complete: %s distinct date(s) generated",
            len(periods),
        )

        populated_ndvi = [period["ndvi"] for period in periods if period["ndvi"] is not None]
        populated_ndmi = [period["ndmi"] for period in periods if period["ndmi"] is not None]
        point_result = {
            "coordinates": (lat, lon),
            "place": place,
            "area": (
                f"{requested_area_description}; {side_m:g} x {side_m:g} m square "
                f"({area_hectares_actual:.2f} ha)"
                if requested_area_description
                else f"{side_m:g} x {side_m:g} m square ({area_hectares_actual:.2f} ha)"
            ),
            "shape": "square",
            "side_m": side_m,
            "area_m2": area_m2,
            "area_hectares": area_hectares_actual,
            "analysis_period": f"{period_start.isoformat()} to {period_end.isoformat()}",
            "periods": periods,
            "pixel_data_file": str(image_root / f"farmbot_pixel_values_point_0_{run_id}.json"),
            "run_id": run_id,
            "ndvi": sum(populated_ndvi) / len(populated_ndvi) if populated_ndvi else None,
            "ndmi": sum(populated_ndmi) / len(populated_ndmi) if populated_ndmi else None,
        }
        Path(point_result["pixel_data_file"]).write_text(
            json.dumps(pixel_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        point_result["soil_moisture"] = point_result["ndmi"]
        point_result["crop_health"] = FarmBotAnalyzer._assess_crop_health(point_result["ndvi"])
        return {"point_0": point_result}

    @staticmethod
    def generate_satellite_report(
        point: Dict,
        weather: Dict,
        ai_analysis: str,
        language: str = "english",
    ) -> str:
        """Create a dated NDVI/NDMI PDF report with sampled pixels and recommendations."""
        filename = Path(tempfile.gettempdir()) / (
            f"farmbot_satellite_report_{point['run_id']}.pdf"
        )
        doc = SimpleDocTemplate(
            str(filename),
            pagesize=A4,
            rightMargin=42,
            leftMargin=42,
            topMargin=42,
            bottomMargin=42,
        )
        styles = getSampleStyleSheet()
        styles["Title"].textColor = colors.HexColor("#1B5E20")
        styles["Heading1"].textColor = colors.HexColor("#1B5E20")
        styles["Heading2"].textColor = colors.HexColor("#2E7D32")
        body = styles["BodyText"]
        body.leading = 14
        location = point.get("place") or (
            f"{point['coordinates'][0]:.6f}, {point['coordinates'][1]:.6f}"
        )
        elements = [
            Paragraph("FarmBot Satellite Crop Monitoring Report", styles["Title"]),
            Spacer(1, 10),
            Paragraph(
                f"Generated: {datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d %H:%M UTC}",
                body,
            ),
            Paragraph(f"Location: {escape(str(location))}", body),
            Paragraph(
                f"Analysis area: {escape(point['area'])}; "
                f"{point['area_m2']:,.0f} m2 ({point['area_hectares']:.2f} ha)",
                body,
            ),
            Paragraph(f"Analysis period: {escape(point['analysis_period'])}", body),
            Paragraph(
                f"Valid Sentinel-2 acquisition dates: {len(point['periods'])}",
                body,
            ),
            Paragraph(
                "Method: Sentinel-2 scenes with CLOUDY_PIXEL_PERCENTAGE below 10% are used; "
                "scenes acquired on the same date are median-composited.",
                body,
            ),
            Spacer(1, 10),
            Paragraph("AI-generated interpretation and recommendations", styles["Heading1"]),
        ]
        for line in (ai_analysis or "AI recommendations were not available.").splitlines():
            line = line.strip()
            if line:
                elements.append(Paragraph(escape(line), body))
        elements.extend([
            Spacer(1, 8),
            Paragraph("Weather at report generation", styles["Heading1"]),
        ])
        if weather.get("not_requested"):
            weather_rows = [["Status", "Weather lookup was not requested"]]
        elif weather.get("error"):
            weather_rows = [["Status", f"Weather unavailable: {weather['error']}"]]
        else:
            weather_rows = [
                ["Observation time", str(weather.get("timestamp", "Not provided"))],
                ["Temperature", f"{weather.get('temperature', 'N/A')} C"],
                ["Humidity", f"{weather.get('humidity', 'N/A')}%"],
                ["Conditions", str(weather.get("conditions", "N/A"))],
                ["Wind speed", f"{weather.get('wind_speed', 'N/A')} km/h"],
                ["Precipitation", f"{weather.get('rain', 'N/A')} mm"],
            ]
        weather_table = Table(
            [["Weather parameter", "Value"], *weather_rows],
            colWidths=[2.0 * inch, 4.7 * inch],
        )
        weather_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8F5E9")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        elements.extend([
            weather_table,
            Spacer(1, 8),
            Paragraph(
                "Weather shown is a current observation at report generation time; "
                "it is not historical weather for each satellite acquisition date.",
                body,
            ),
            Spacer(1, 10),
            Paragraph("Per-date satellite measurements", styles["Heading1"]),
        ])
        date_rows = [["Date", "Scenes", "Mean NDVI", "Mean NDMI", "Pixels sampled"]]
        for item in point["periods"]:
            date_rows.append([
                item["selected_date"],
                str(item["image_count"]),
                "N/A" if item["ndvi"] is None else f"{item['ndvi']:.3f}",
                "N/A" if item["ndmi"] is None else f"{item['ndmi']:.3f}",
                f"{len(item['ndvi_pixels'])} / {len(item['ndmi_pixels'])}",
            ])
        dates_table = Table(
            date_rows,
            repeatRows=1,
            colWidths=[1.15 * inch, 0.7 * inch, 1.05 * inch, 1.05 * inch, 1.35 * inch],
        )
        dates_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8F5E9")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.extend([
            dates_table,
            Spacer(1, 8),
            Paragraph(
                "NDMI indicates vegetation/canopy moisture and is not a direct soil-moisture measurement. "
                "All valid 10 m pixel samples per index/date are included in the attached JSON file.",
                body,
            ),
        ])

        for item in point["periods"]:
            elements.append(PageBreak())
            elements.append(Paragraph(
                f"Satellite maps and sampled pixels — {escape(item['selected_date'])}",
                styles["Heading1"],
            ))
            elements.append(Paragraph(
                f"Sentinel-2 scenes: {item['image_count']} | "
                f"Mean NDVI: {item['ndvi'] if item['ndvi'] is not None else 'N/A'} | "
                f"Mean NDMI: {item['ndmi'] if item['ndmi'] is not None else 'N/A'}",
                body,
            ))
            map_cells = []
            for index_name in ("ndvi", "ndmi"):
                map_path = item.get(f"{index_name}_image")
                if map_path and Path(map_path).is_file():
                    map_cells.append([
                        Paragraph(index_name.upper(), styles["Heading2"]),
                        Image(map_path, width=3.0 * inch, height=3.0 * inch),
                    ])
                else:
                    map_cells.append([Paragraph(f"{index_name.upper()}: no map", body)])
            maps_table = Table([map_cells], colWidths=[3.2 * inch, 3.2 * inch])
            maps_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]))
            elements.append(maps_table)
            pixel_rows = [["Index", "Sampled pixels", "Value range"]]
            for index_name in ("ndvi", "ndmi"):
                values = [
                    pixel[index_name.upper()]
                    for pixel in item[f"{index_name}_pixels"]
                ]
                value_range = (
                    f"{min(values):.3f} to {max(values):.3f}"
                    if values else "No valid samples"
                )
                pixel_rows.append([index_name.upper(), str(len(values)), value_range])
            pixel_table = Table(pixel_rows, colWidths=[1.4 * inch, 1.5 * inch, 2.5 * inch])
            pixel_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8F5E9")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ]))
            elements.extend([
                Spacer(1, 8),
                Paragraph("Pixel sample summary (full coordinates and values are in attached JSON)", styles["Heading2"]),
                pixel_table,
            ])

        doc.build(elements)
        return str(filename)

    @staticmethod
    def _shift_months(value: datetime.date, months: int) -> datetime.date:
        month_index = value.year * 12 + value.month - 1 + months
        year, month = divmod(month_index, 12)
        day = min(value.day, calendar.monthrange(year, month + 1)[1])
        return value.replace(year=year, month=month + 1, day=day)

    @staticmethod
    def _geocode_place(place: str) -> Tuple[float, float, str]:
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": place, "format": "jsonv2", "limit": 1},
            headers={"User-Agent": "FarmBot-GIS-Agent/0.1"},
            timeout=15,
        )
        response.raise_for_status()
        results = response.json()
        if not results:
            raise ValueError(f"Could not find a map location for: {place}")
        result = results[0]
        return float(result["lat"]), float(result["lon"]), result.get("display_name", place)
        
    @staticmethod
    def _assess_crop_health(ndvi_value: float) -> str:
        """Assess crop health based on NDVI value"""
        if not isinstance(ndvi_value, (int, float)):
            return "Unknown"
            
        if ndvi_value > 0.7: return "Excellent"
        elif ndvi_value > 0.5: return "Good"
        elif ndvi_value > 0.3: return "Moderate"
        else: return "Poor"
        
    @staticmethod
    def generate_pdf_report(data: Dict, instructions: Dict = None) -> str:
        """Generate professional PDF report from analysis data with enhanced error handling"""
        if instructions is None:
            instructions = {}
        
        # Enhanced input validation
        if not data or not isinstance(data, dict):
            raise ValueError("Invalid data format - expected dictionary with analysis results")
        
        # Check if data contains any point data (even with errors)
        if not data:
            raise ValueError("Empty analysis data - no points to generate report for")
        
        # Check if we have any valid points (either successful analyses or errors)
        has_any_data = False
        has_valid_data = False
        error_messages = []
        
        for point_key, point_data in data.items():
            if not isinstance(point_data, dict):
                error_messages.append(f"Invalid data format for {point_key}")
                continue
            
            has_any_data = True
            
            if 'error' not in point_data:
                has_valid_data = True
        
        if not has_any_data:
            raise ValueError("No valid point data structure found in input")
        
        # Create PDF even if we only have error messages (but include them in report)
        filename = os.path.join(tempfile.gettempdir(), "farmbot_analysis_report.pdf")
        doc = SimpleDocTemplate(filename, pagesize=A4,
                            rightMargin=72, leftMargin=72,
                            topMargin=72, bottomMargin=72)
        
        styles = getSampleStyleSheet()
        
        # Custom styles
        styles['Title'].fontName = 'Helvetica-Bold'
        styles['Title'].fontSize = 18
        styles['Title'].leading = 22
        styles['Title'].alignment = 1
        styles['Title'].spaceAfter = 20
        
        if 'Heading1' not in styles:
            styles.add(ParagraphStyle(name='Heading1', 
                                fontSize=14, 
                                leading=18, 
                                spaceAfter=12,
                                fontName='Helvetica-Bold',
                                textColor=colors.HexColor('#2E7D32')))
        
        if 'Heading2' not in styles:
            styles.add(ParagraphStyle(name='Heading2', 
                                fontSize=12, 
                                leading=16, 
                                spaceAfter=8,
                                fontName='Helvetica-Bold',
                                textColor=colors.HexColor('#2E7D32')))
        
        if 'BodyText' not in styles:
            styles.add(ParagraphStyle(name='BodyText', 
                                fontSize=10, 
                                leading=14,
                                spaceAfter=6))
        
        if 'Footer' not in styles:
            styles.add(ParagraphStyle(name='Footer', 
                                fontSize=8, 
                                leading=10,
                                textColor=colors.grey))
        
        # Create elements for the PDF
        elements = []
        
        # Add cover page
        elements.append(Paragraph("FarmBot Analysis Report", styles['Title']))
        elements.append(Spacer(1, 0.5*inch))
        
        if not has_valid_data:
            warning_style = ParagraphStyle(
                name='Warning',
                parent=styles['BodyText'],
                textColor=colors.red,
                fontSize=12,
                leading=14
            )
            elements.append(Paragraph("WARNING: Limited Report Data Available", warning_style))
            elements.append(Spacer(1, 0.2*inch))
        
        elements.append(Paragraph(f"Generated on: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['BodyText']))
        elements.append(Spacer(1, 0.5*inch))
        
        # Add error summary page if there were errors
        if error_messages or not has_valid_data:
            elements.append(Paragraph("Analysis Issues Encountered", styles['Heading1']))
            elements.append(Spacer(1, 0.2*inch))
            
            if error_messages:
                for msg in error_messages:
                    elements.append(Paragraph(f"• {msg}", styles['BodyText']))
            else:
                for point_key, point_data in data.items():
                    if isinstance(point_data, dict) and 'error' in point_data:
                        loc = point_data.get('coordinates', 'Unknown location')
                        elements.append(Paragraph(
                            f"• {loc}: {point_data['error']}", 
                            styles['BodyText']
                        ))
            
            elements.append(Spacer(1, 0.3*inch))
            elements.append(Paragraph(
                "Note: Some analyses may be incomplete due to the above issues. "
                "Please verify your input parameters and try again.",
                styles['BodyText']
            ))
            elements.append(PageBreak())
        
        # Add analysis for each point
        for point_key, point_data in data.items():
            if not isinstance(point_data, dict) or 'error' in point_data:
                continue
                
            # Point header
            elements.append(Paragraph(f"Analysis for Location: {point_data.get('coordinates', 'Unknown')}", styles['Heading1']))
            elements.append(Spacer(1, 0.2*inch))
            
            # Analysis period
            elements.append(Paragraph(f"Analysis Period: {point_data.get('analysis_period', 'N/A')}", styles['BodyText']))
            elements.append(Spacer(1, 0.2*inch))
            
            # NDVI Analysis
            if 'ndvi' in point_data and isinstance(point_data['ndvi'], (int, float)):
                # Create NDVI chart
                fig, ax = plt.subplots(figsize=(6, 3))
                ndvi_value = point_data['ndvi']
                health_status = point_data.get('crop_health', 'Unknown')
                
                # Create NDVI scale visualization
                colors_ndvi = ['#d73027', '#fc8d59', '#fee08b', '#d9ef8b', '#91cf60', '#1a9850']
                positions = [0, 0.2, 0.4, 0.6, 0.8, 1.0]
                
                for i in range(len(colors_ndvi)-1):
                    ax.fill_between([positions[i], positions[i+1]], 0, 1, 
                                  color=colors_ndvi[i], alpha=0.7)
                
                ax.plot([ndvi_value, ndvi_value], [0, 1], 'k-', lw=2)
                ax.text(ndvi_value+0.02, 0.5, f'{ndvi_value:.2f}\n({health_status})', 
                       va='center', fontsize=10)
                
                ax.set_xlim(0, 1)
                ax.set_ylim(0, 1)
                ax.set_xticks(positions)
                ax.set_xticklabels(['0.0', '0.2', '0.4', '0.6', '0.8', '1.0'])
                ax.set_yticks([])
                ax.set_title('NDVI Scale with Current Value', fontsize=10)
                ax.spines['top'].set_visible(False)
                ax.spines['right'].set_visible(False)
                ax.spines['left'].set_visible(False)
                
                # Save the plot to a temporary file
                chart_path = os.path.join(tempfile.gettempdir(), f"ndvi_chart_{point_key}.png")
                plt.savefig(chart_path, dpi=300, bbox_inches='tight', transparent=True)
                plt.close()
                
                # Add chart to PDF
                elements.append(Paragraph("Crop Health Analysis (NDVI)", styles['Heading2']))
                elements.append(Spacer(1, 0.1*inch))
                elements.append(Image(chart_path, width=5*inch, height=2.5*inch))
                elements.append(Spacer(1, 0.2*inch))
                
                # NDVI interpretation
                ndvi_interpretation = {
                    'Excellent': 'Crops are very healthy with excellent growth',
                    'Good': 'Crops are healthy but could improve',
                    'Moderate': 'Crops show some issues needing attention',
                    'Poor': 'Crops are in poor condition, need immediate action'
                }.get(health_status, 'NDVI analysis not available')
                
                elements.append(Paragraph(f"<b>Interpretation:</b> {ndvi_interpretation}", styles['BodyText']))
                elements.append(Spacer(1, 0.3*inch))
            
            # Soil moisture analysis
            if 'soil_moisture' in point_data and isinstance(point_data['soil_moisture'], (int, float)):
                moisture_value = point_data['soil_moisture']
                elements.append(Paragraph("Soil Moisture Analysis", styles['Heading2']))
                elements.append(Spacer(1, 0.1*inch))
                
                # Create moisture meter
                fig, ax = plt.subplots(figsize=(6, 1))
                moisture_percent = min(max(moisture_value * 100, 0), 100)
                
                # Create gradient bar
                cmap = plt.get_cmap('YlGnBu')
                gradient = np.linspace(0, 1, 256).reshape(1, -1)
                ax.imshow(gradient, aspect='auto', cmap=cmap, extent=[0, 100, 0, 1])
                
                # Add indicator
                ax.plot([moisture_percent, moisture_percent], [0, 1], 'k-', lw=2)
                ax.text(moisture_percent+2, 0.5, f'{moisture_percent:.1f}%', 
                       va='center', fontsize=10)
                
                ax.set_xlim(0, 100)
                ax.set_ylim(0, 1)
                ax.set_yticks([])
                ax.set_xticks([0, 25, 50, 75, 100])
                ax.set_xticklabels(['Very Dry', 'Dry', 'Optimal', 'Wet', 'Very Wet'])
                ax.set_title('Soil Moisture Level', fontsize=10)
                ax.spines['top'].set_visible(False)
                ax.spines['right'].set_visible(False)
                ax.spines['left'].set_visible(False)
                
                # Save the plot
                moisture_chart_path = os.path.join(tempfile.gettempdir(), f"moisture_chart_{point_key}.png")
                plt.savefig(moisture_chart_path, dpi=300, bbox_inches='tight', transparent=True)
                plt.close()
                
                elements.append(Image(moisture_chart_path, width=5*inch, height=1*inch))
                elements.append(Spacer(1, 0.1*inch))
                
                # Moisture recommendations
                if moisture_percent < 30:
                    rec = "Soil is too dry. Immediate irrigation needed."
                elif moisture_percent < 50:
                    rec = "Soil is somewhat dry. Consider irrigation soon."
                elif moisture_percent < 70:
                    rec = "Soil moisture is at optimal levels."
                else:
                    rec = "Soil is too wet. Reduce irrigation to prevent waterlogging."
                
                elements.append(Paragraph(f"<b>Recommendation:</b> {rec}", styles['BodyText']))
                elements.append(Spacer(1, 0.3*inch))
            
            # Weather data
            if 'weather' in point_data and point_data['weather']:
                weather = point_data['weather']
                elements.append(Paragraph("Weather Conditions", styles['Heading2']))
                elements.append(Spacer(1, 0.1*inch))
                
                weather_data = [
                    ["Parameter", "Value"],
                    ["Temperature", f"{weather.get('temperature', 'N/A')}°C"],
                    ["Humidity", f"{weather.get('humidity', 'N/A')}%"],
                    ["Wind Speed", f"{weather.get('wind_speed', 'N/A')} km/h"],
                    ["Conditions", weather.get('conditions', 'N/A').capitalize()],
                    ["Rainfall (last hour)", f"{weather.get('rain', 0)}mm"]
                ]
                
                weather_table = Table(weather_data, colWidths=[2*inch, 3*inch])
                weather_table.setStyle(TableStyle([
                    ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
                    ('FONTSIZE', (0,0), (-1,-1), 10),
                    ('BOTTOMPADDING', (0,0), (-1,-1), 6),
                    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#E3F2FD')),
                    ('GRID', (0,0), (-1,-1), 1, colors.lightgrey)
                ]))
                elements.append(weather_table)
                elements.append(Spacer(1, 0.3*inch))
                
                # Weather impact analysis
                temp = weather.get('temperature')
                if isinstance(temp, (int, float)):
                    if temp > 35:
                        impact = "High temperatures may stress crops. Provide shade and ensure adequate water."
                    elif temp < 10:
                        impact = "Low temperatures may damage crops. Consider protective measures."
                    else:
                        impact = "Temperatures are in optimal range for most crops."
                    
                    elements.append(Paragraph(f"<b>Weather Impact:</b> {impact}", styles['BodyText']))
                    elements.append(Spacer(1, 0.3*inch))
            
            # Recommendations section
            elements.append(Paragraph("Farm Management Recommendations", styles['Heading1']))
            elements.append(Spacer(1, 0.2*inch))
            
            # Generate recommendations based on analysis
            recommendations = []
            
            # NDVI-based recommendations
            if 'crop_health' in point_data:
                health = point_data['crop_health']
                if health == 'Poor':
                    recommendations.extend([
                        "✓ Apply fertilizer urgently",
                        "✓ Check for pests and diseases",
                        "✓ Test soil for nutrient deficiencies",
                        "✓ Increase irrigation if needed"
                    ])
                elif health == 'Moderate':
                    recommendations.extend([
                        "✓ Apply balanced fertilizer",
                        "✓ Monitor for early signs of pests",
                        "✓ Maintain proper irrigation",
                        "✓ Consider foliar feeding"
                    ])
                elif health == 'Good':
                    recommendations.extend([
                        "✓ Continue current practices",
                        "✓ Monitor crop health regularly",
                        "✓ Prepare for next growth stage"
                    ])
                else:  # Excellent
                    recommendations.extend([
                        "✓ Maintain excellent practices",
                        "✓ Document management strategies",
                        "✓ Explore intercropping options"
                    ])
            
            # Soil moisture recommendations
            if 'soil_moisture' in point_data and isinstance(point_data['soil_moisture'], (int, float)):
                moisture = point_data['soil_moisture']
                if moisture < 0.3:
                    recommendations.append("✓ Increase irrigation frequency immediately")
                elif moisture > 0.7:
                    recommendations.append("✓ Reduce irrigation to prevent waterlogging")
            
            # Weather-based recommendations
            if 'weather' in point_data:
                weather = point_data['weather']
                temp = weather.get('temperature')
                rain = weather.get('rain', 0)
                
                if isinstance(temp, (int, float)):
                    if temp > 35:
                        recommendations.append("✓ Use mulch or shade to reduce soil temperature")
                    if temp < 10:
                        recommendations.append("✓ Use protective covers for cold protection")
                
                if isinstance(rain, (int, float)) and rain > 10:
                    recommendations.append("✓ Ensure proper drainage to prevent waterlogging")
            
            # Add recommendations to PDF
            if recommendations:
                for rec in recommendations:
                    elements.append(Paragraph(f"• {rec}", styles['BodyText']))
            else:
                elements.append(Paragraph("No specific recommendations available based on current analysis.", styles['BodyText']))
            
            elements.append(Spacer(1, 0.3*inch))
            
            # Government schemes section
            elements.append(Paragraph("Applicable Government Schemes", styles['Heading2']))
            elements.append(Spacer(1, 0.1*inch))
            
            schemes = GovernmentSchemes.get_scheme_info(None, 'english')
            if schemes and isinstance(schemes, list):
                for scheme in schemes[:3]:  # Show top 3 relevant schemes
                    elements.append(Paragraph(f"<b>{scheme.get('name', 'N/A')}</b>", styles['BodyText']))
                    elements.append(Paragraph(scheme.get('description', 'No description available'), styles['BodyText']))
                    elements.append(Spacer(1, 0.1*inch))
            else:
                elements.append(Paragraph("Contact local agriculture office for scheme information.", styles['BodyText']))
            
            elements.append(Spacer(1, 0.5*inch))
            
            # Add page break if not last point
            if point_key != list(data.keys())[-1]:
                elements.append(PageBreak())
        
        # Add footer to each page
        def add_footer(canvas, doc):
            canvas.saveState()
            canvas.setFont('Helvetica', 8)
            status = "Partial" if not has_valid_data else "Complete"
            canvas.drawString(inch, 0.75*inch, 
                         f"FarmBot Analysis Report ({status}) • {datetime.datetime.now().strftime('%Y-%m-%d')} • Page {doc.page}")
            canvas.restoreState()
        
        # Build the PDF
        doc.build(elements, onFirstPage=add_footer, onLaterPages=add_footer)
        
        return filename