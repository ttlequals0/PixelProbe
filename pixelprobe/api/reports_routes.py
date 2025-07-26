from flask import Blueprint, request, jsonify, send_file, make_response
import os
import json
import logging
from datetime import datetime, timezone
from io import BytesIO
import base64

from models import db, ScanReport
from pixelprobe.utils.security import validate_json_input

logger = logging.getLogger(__name__)

reports_bp = Blueprint('reports', __name__, url_prefix='/api')

@reports_bp.route('/scan-reports')
def get_scan_reports():
    """Get paginated scan reports with optional filters"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    scan_type = request.args.get('scan_type', 'all')
    status = request.args.get('status', 'all')
    sort_order = request.args.get('sort_order', 'desc')
    
    # Build query
    query = ScanReport.query
    
    # Apply filters
    if scan_type != 'all':
        query = query.filter_by(scan_type=scan_type)
    
    if status != 'all':
        query = query.filter_by(status=status)
    
    # Apply sorting (always by start_time)
    if sort_order.lower() == 'asc':
        query = query.order_by(ScanReport.start_time.asc())
    else:
        query = query.order_by(ScanReport.start_time.desc())
    
    # Paginate
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    # Build response
    reports = []
    for report in pagination.items:
        report_dict = report.to_dict()
        
        # Add human-readable duration
        if report.duration_seconds:
            hours = int(report.duration_seconds // 3600)
            minutes = int((report.duration_seconds % 3600) // 60)
            seconds = int(report.duration_seconds % 60)
            
            if hours > 0:
                report_dict['duration_formatted'] = f"{hours}h {minutes}m {seconds}s"
            elif minutes > 0:
                report_dict['duration_formatted'] = f"{minutes}m {seconds}s"
            else:
                report_dict['duration_formatted'] = f"{seconds}s"
        else:
            report_dict['duration_formatted'] = 'N/A'
        
        reports.append(report_dict)
    
    return jsonify({
        'reports': reports,
        'total': pagination.total,
        'page': page,
        'per_page': per_page,
        'pages': pagination.pages
    })

@reports_bp.route('/scan-reports/<report_id>')
def get_scan_report(report_id):
    """Get a single scan report by ID"""
    report = ScanReport.query.filter_by(report_id=report_id).first()
    if not report:
        return jsonify({'error': 'Report not found'}), 404
    
    report_dict = report.to_dict()
    
    # Add additional computed fields
    if report.duration_seconds:
        hours = int(report.duration_seconds // 3600)
        minutes = int((report.duration_seconds % 3600) // 60)
        seconds = int(report.duration_seconds % 60)
        
        if hours > 0:
            report_dict['duration_formatted'] = f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            report_dict['duration_formatted'] = f"{minutes}m {seconds}s"
        else:
            report_dict['duration_formatted'] = f"{seconds}s"
    
    # Add summary statistics
    if report.scan_type in ['full_scan', 'rescan', 'deep_scan']:
        report_dict['summary'] = {
            'total_files': report.files_scanned,
            'new_files': report.files_added,
            'updated_files': report.files_updated,
            'corrupted_files': report.files_corrupted,
            'files_with_warnings': report.files_with_warnings,
            'error_files': report.files_error,
            'success_rate': round((1 - (report.files_corrupted / report.files_scanned)) * 100, 2) if report.files_scanned > 0 else 100
        }
    elif report.scan_type == 'cleanup':
        report_dict['summary'] = {
            'orphaned_found': report.orphaned_records_found,
            'orphaned_deleted': report.orphaned_records_deleted,
            'cleanup_rate': round((report.orphaned_records_deleted / report.orphaned_records_found) * 100, 2) if report.orphaned_records_found > 0 else 0
        }
    elif report.scan_type == 'file_changes':
        report_dict['summary'] = {
            'files_changed': report.files_changed,
            'new_corruptions': report.files_corrupted_new,
            'total_files_checked': report.files_scanned
        }
    
    return jsonify(report_dict)

@reports_bp.route('/scan-reports/<report_id>/export')
def export_scan_report(report_id):
    """Export scan report as JSON"""
    report = ScanReport.query.filter_by(report_id=report_id).first()
    if not report:
        return jsonify({'error': 'Report not found'}), 404
    
    report_dict = report.to_dict()
    
    # Add metadata
    report_dict['export_metadata'] = {
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'export_format': 'json',
        'version': '1.0'
    }
    
    # Create JSON file
    json_data = json.dumps(report_dict, indent=2)
    
    # Create response
    response = make_response(json_data)
    response.headers['Content-Type'] = 'application/json'
    response.headers['Content-Disposition'] = f'attachment; filename=scan_report_{report_id}_{report.start_time.strftime("%Y%m%d_%H%M%S")}.json'
    
    return response

@reports_bp.route('/generate-pdf-report/<scan_type>/<scan_id>')
def generate_pdf_report(scan_type, scan_id):
    """Generate PDF report for scan results with tool and details"""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter, landscape
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.lib.enums import TA_CENTER
        
        # Create PDF buffer
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), 
                              topMargin=0.5*inch, bottomMargin=0.5*inch,
                              leftMargin=0.5*inch, rightMargin=0.5*inch)
        
        # Container for the 'Flowable' objects
        elements = []
        
        # Define styles with PixelProbe color scheme
        styles = getSampleStyleSheet()
        primary_green = colors.HexColor('#1ce783')
        primary_black = colors.HexColor('#040405')
        gradient_end = colors.HexColor('#183949')
        
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=primary_black,
            spaceAfter=30,
            alignment=TA_CENTER
        )
        
        # Create styles for wrapping text in table cells
        cell_style = ParagraphStyle(
            'CellStyle',
            parent=styles['Normal'],
            fontSize=6,
            leading=7
        )
        
        # Add logo and title
        try:
            logo_path = os.path.join(os.path.dirname(__file__), '../../static/images/pixelprobe-logo.png')
            if os.path.exists(logo_path):
                # Logo is 670x729 pixels, maintain aspect ratio
                logo_width = 1.5*inch
                logo_height = logo_width * (729.0/670.0)  # Maintain aspect ratio
                logo = Image(logo_path, width=logo_width, height=logo_height)
                logo.hAlign = 'CENTER'
                elements.append(logo)
                elements.append(Spacer(1, 0.2*inch))
        except Exception as e:
            logger.debug(f"Could not add logo to PDF: {e}")
        
        # Add title
        elements.append(Paragraph("PixelProbe Scan Report", title_style))
        elements.append(Spacer(1, 0.2*inch))
        
        # Add export info
        info_text = f"Report Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}<br/>"
        info_text += f"Scan Type: {scan_type.replace('_', ' ').title()}<br/>"
        info_text += f"Scan ID: {scan_id}"
        elements.append(Paragraph(info_text, styles['Normal']))
        elements.append(Spacer(1, 0.2*inch))
        
        # Query scan results based on scan type
        from models import ScanResult
        if scan_type == 'rescan' and '_' in scan_id:
            # For rescan, parse the file path from scan_id
            file_path = scan_id.replace('_', '/')
            results = ScanResult.query.filter_by(file_path=file_path).all()
        else:
            # For other scan types, get recent results
            results = ScanResult.query.filter(
                ScanResult.scan_date.isnot(None)
            ).order_by(ScanResult.scan_date.desc()).limit(500).all()
        
        if not results:
            elements.append(Paragraph("No scan results found.", styles['Normal']))
        else:
            # Create table header with all required fields
            table_data = [['Status', 'File Path', 'Size', 'Type', 'Tool', 'Details', 'Scan Date']]
            
            for result in results:
                # Determine status
                status = 'Corrupted' if result.is_corrupted and not result.marked_as_good else 'Healthy'
                if getattr(result, 'has_warnings', False) and not result.marked_as_good:
                    status = 'Warning'
                
                # Format file size
                size = f"{result.file_size / (1024*1024):.2f} MB" if result.file_size else 'N/A'
                
                # Get file type
                file_type = result.file_type or 'Unknown'
                
                # Get scan tool - use actual tool from database
                scan_tool = getattr(result, 'scan_tool', None) or 'N/A'
                
                # Get details - combine corruption details, warnings, and scan output
                details = []
                if result.corruption_details:
                    details.append(result.corruption_details)
                if getattr(result, 'warning_details', None):
                    details.append(getattr(result, 'warning_details', ''))
                if getattr(result, 'scan_output', None):
                    # Extract key information from scan output
                    scan_output = getattr(result, 'scan_output', '')
                    if 'Video stream:' in scan_output:
                        for line in scan_output.split('\\n'):
                            if 'Video stream:' in line or 'Duration:' in line:
                                details.append(line.strip())
                                break
                
                details_text = ' '.join(details) if details else ''
                
                # Format scan date
                scan_date = result.scan_date.strftime('%m/%d/%Y, %I:%M:%S %p') if result.scan_date else 'N/A'
                
                # Wrap file path and details in Paragraph for proper text wrapping
                file_path_para = Paragraph(result.file_path, cell_style)
                details_para = Paragraph(details_text, cell_style)
                
                table_data.append([
                    status,
                    file_path_para,
                    size,
                    file_type,
                    scan_tool,
                    details_para,
                    scan_date
                ])
            
            # Create table with proper column widths
            table = Table(table_data, colWidths=[0.7*inch, 3.5*inch, 0.7*inch, 0.8*inch, 0.6*inch, 2.5*inch, 1.2*inch])
            table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('BACKGROUND', (0, 0), (-1, 0), primary_green),
                ('TEXTCOLOR', (0, 0), (-1, 0), primary_black),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                ('PADDING', (0, 0), (-1, -1), 4),
            ]))
            
            elements.append(table)
            
            if len(results) >= 500:
                elements.append(Spacer(1, 0.1*inch))
                elements.append(Paragraph("Note: Showing first 500 results", styles['Normal']))
        
        # Build PDF
        doc.build(elements)
        
        # Get PDF data
        pdf_data = buffer.getvalue()
        buffer.close()
        
        # Create filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"scan_report_{scan_type}_{scan_id}_{timestamp}.pdf"
        
        # Create response
        response = make_response(pdf_data)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        
        return response
        
    except ImportError:
        logger.error("reportlab not installed for PDF export")
        return jsonify({'error': 'PDF export requires reportlab package'}), 500
    except Exception as e:
        logger.error(f"Error generating PDF report: {e}")
        return jsonify({'error': f'Failed to generate PDF: {str(e)}'}), 500

@reports_bp.route('/scan-reports/<report_id>/pdf')
def export_scan_report_pdf(report_id):
    """Export scan report as PDF for compliance"""
    report = ScanReport.query.filter_by(report_id=report_id).first()
    if not report:
        return jsonify({'error': 'Report not found'}), 404
    
    try:
        # Import reportlab for PDF generation
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter, A4
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT
        
        # Create PDF buffer
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
        
        # Container for the 'Flowable' objects
        elements = []
        
        # Define styles with PixelProbe color scheme
        styles = getSampleStyleSheet()
        # Primary green color
        primary_green = colors.HexColor('#1ce783')
        primary_black = colors.HexColor('#040405')
        gradient_end = colors.HexColor('#183949')
        
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=primary_black,
            spaceAfter=30,
            alignment=TA_CENTER
        )
        
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=16,
            textColor=gradient_end,
            spaceAfter=12
        )
        
        # Add logo and title
        try:
            logo_path = os.path.join(os.path.dirname(__file__), '../../static/images/pixelprobe-logo.png')
            if os.path.exists(logo_path):
                # Logo is 670x729 pixels, maintain aspect ratio
                logo_width = 1.5*inch
                logo_height = logo_width * (729.0/670.0)  # Maintain aspect ratio
                logo = Image(logo_path, width=logo_width, height=logo_height)
                logo.hAlign = 'CENTER'
                elements.append(logo)
                elements.append(Spacer(1, 0.2*inch))
        except Exception as e:
            logger.debug(f"Could not add logo to PDF: {e}")
        
        # Add title
        elements.append(Paragraph("PixelProbe Scan Report", title_style))
        elements.append(Spacer(1, 0.2*inch))
        
        # Add report metadata
        elements.append(Paragraph("Report Information", heading_style))
        
        # Report info table
        report_info = [
            ['Report ID:', report.report_id],
            ['Scan Type:', report.scan_type.replace('_', ' ').title()],
            ['Status:', report.status.title()],
            ['Start Time:', report.start_time.strftime('%Y-%m-%d %H:%M:%S UTC') if report.start_time else 'N/A'],
            ['End Time:', report.end_time.strftime('%Y-%m-%d %H:%M:%S UTC') if report.end_time else 'N/A'],
            ['Duration:', f"{int(report.duration_seconds // 60)}m {int(report.duration_seconds % 60)}s" if report.duration_seconds else 'N/A'],
        ]
        
        if report.directories_scanned:
            try:
                # Log the raw value for debugging
                logger.debug(f"Raw directories_scanned: {report.directories_scanned}")
                
                dirs = json.loads(report.directories_scanned)
                logger.debug(f"Parsed dirs type: {type(dirs)}, value: {dirs}")
                
                # Handle case where dirs might be a string instead of list
                if isinstance(dirs, str):
                    # Check if it looks like a comma-separated string
                    if ',' in dirs:
                        # Split by comma and clean up
                        dirs_list = [d.strip() for d in dirs.split(',')]
                        dirs_text = '\n'.join(dirs_list)
                    else:
                        dirs_text = dirs
                elif isinstance(dirs, list):
                    # If it's a list, join with newlines
                    dirs_text = '\n'.join(str(d) for d in dirs)
                else:
                    # Fallback for other types
                    dirs_text = str(dirs)
                
                report_info.append(['Directories:', dirs_text])
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse directories_scanned: {e}")
                # If JSON parsing fails, use the raw value
                report_info.append(['Directories:', report.directories_scanned])
        
        # Convert text fields to Paragraph objects for better formatting
        formatted_info = []
        for row in report_info:
            label = row[0]
            value = row[1]
            # Use Paragraph for multi-line text (directories)
            if '\n' in str(value):
                value = Paragraph(value.replace('\n', '<br/>'), styles['Normal'])
            formatted_info.append([label, value])
        
        info_table = Table(formatted_info, colWidths=[2*inch, 4*inch])
        info_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
            ('ALIGN', (1, 0), (1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f8f9fa')),
            ('PADDING', (0, 0), (-1, -1), 8),
        ]))
        
        elements.append(info_table)
        elements.append(Spacer(1, 0.3*inch))
        
        # Add scan statistics
        elements.append(Paragraph("Scan Statistics", heading_style))
        
        if report.scan_type in ['full_scan', 'rescan', 'deep_scan']:
            stats_data = [
                ['Metric', 'Value'],
                ['Total Files Discovered', f"{report.total_files_discovered:,}"],
                ['Files Scanned', f"{report.files_scanned:,}"],
                ['New Files Added', f"{report.files_added:,}"],
                ['Files Updated', f"{report.files_updated:,}"],
                ['Corrupted Files', f"{report.files_corrupted:,}"],
                ['Files with Warnings', f"{report.files_with_warnings:,}"],
                ['Files with Errors', f"{report.files_error:,}"],
            ]
            
            # Add success rate
            if report.files_scanned > 0:
                success_rate = (1 - (report.files_corrupted / report.files_scanned)) * 100
                stats_data.append(['Success Rate', f"{success_rate:.2f}%"])
            
        elif report.scan_type == 'cleanup':
            stats_data = [
                ['Metric', 'Value'],
                ['Orphaned Records Found', f"{report.orphaned_records_found:,}"],
                ['Orphaned Records Deleted', f"{report.orphaned_records_deleted:,}"],
            ]
            
            if report.orphaned_records_found > 0:
                cleanup_rate = (report.orphaned_records_deleted / report.orphaned_records_found) * 100
                stats_data.append(['Cleanup Rate', f"{cleanup_rate:.2f}%"])
        
        elif report.scan_type == 'file_changes':
            stats_data = [
                ['Metric', 'Value'],
                ['Files Checked', f"{report.files_scanned:,}"],
                ['Files Changed', f"{report.files_changed:,}"],
                ['New Corruptions', f"{report.files_corrupted_new:,}"],
            ]
        
        stats_table = Table(stats_data, colWidths=[3*inch, 2*inch])
        stats_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('BACKGROUND', (0, 0), (-1, 0), primary_green),
            ('TEXTCOLOR', (0, 0), (-1, 0), primary_black),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
            ('PADDING', (0, 0), (-1, -1), 8),
        ]))
        
        elements.append(stats_table)
        elements.append(Spacer(1, 0.3*inch))
        
        # Add compliance footer
        elements.append(Spacer(1, 0.5*inch))
        footer_style = ParagraphStyle(
            'Footer',
            parent=styles['Normal'],
            fontSize=8,
            textColor=colors.HexColor('#7f8c8d'),
            alignment=TA_CENTER
        )
        
        # Create styles for wrapping text in table cells
        cell_style = ParagraphStyle(
            'CellStyle',
            parent=styles['Normal'],
            fontSize=6,
            leading=7
        )
        
        # Add scanned files list
        if report.scan_type in ['full_scan', 'rescan', 'deep_scan']:
            elements.append(PageBreak())
            elements.append(Paragraph("Scanned Files", heading_style))
            
            # Query files scanned during this scan period
            from models import ScanResult
            scanned_files = ScanResult.query.filter(
                ScanResult.scan_date >= report.start_time,
                ScanResult.scan_date <= (report.end_time or datetime.now(timezone.utc))
            ).order_by(ScanResult.file_path).all()
            
            if scanned_files:
                # Create files table header
                files_data = [['File Path', 'Status', 'Size', 'Type', 'Scan Date']]
                
                # Add file rows (limit to first 500 for PDF size)
                for file in scanned_files[:500]:
                    status = 'Corrupted' if file.is_corrupted and not file.marked_as_good else 'Healthy'
                    size = f"{file.file_size / (1024*1024):.2f} MB" if file.file_size else 'N/A'
                    file_type = file.file_type or 'Unknown'
                    scan_date = file.scan_date.strftime('%Y-%m-%d %H:%M') if file.scan_date else 'N/A'
                    
                    files_data.append([
                        file.file_path,  # Show full path without truncation
                        status,
                        size,
                        file_type,
                        scan_date
                    ])
                
                # Create files table
                files_table = Table(files_data, colWidths=[3.5*inch, 0.8*inch, 0.8*inch, 1.2*inch, 1.2*inch])
                files_table.setStyle(TableStyle([
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                    ('FONTSIZE', (0, 0), (-1, -1), 8),
                    ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                    ('BACKGROUND', (0, 0), (-1, 0), gradient_end),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                    ('PADDING', (0, 0), (-1, -1), 4),
                ]))
                
                elements.append(files_table)
                
                if len(scanned_files) > 500:
                    elements.append(Spacer(1, 0.1*inch))
                    elements.append(Paragraph(f"Note: Showing first 500 of {len(scanned_files)} total files", footer_style))
            else:
                elements.append(Paragraph("No files were scanned during this scan.", styles['Normal']))
        
        elements.append(Spacer(1, 0.5*inch))
        elements.append(Paragraph(f"Generated on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}", footer_style))
        elements.append(Paragraph("This report is for compliance and auditing purposes", footer_style))
        
        # Build PDF
        doc.build(elements)
        
        # Get PDF data
        pdf_data = buffer.getvalue()
        buffer.close()
        
        # Create response
        response = make_response(pdf_data)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=scan_report_{report_id}_{report.start_time.strftime("%Y%m%d_%H%M%S")}.pdf'
        
        return response
        
    except ImportError as e:
        # Log the import error (use module-level logger)
        logger.error(f"Failed to import reportlab: {e}")
        # If reportlab is not installed, return a simple HTML version that can be printed to PDF
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Scan Report {report.report_id}</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; background-color: #f8f9fa; }}
                h1 {{ color: #040405; text-align: center; }}
                h2 {{ color: #183949; margin-top: 30px; }}
                table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
                th, td {{ border: 1px solid #dee2e6; padding: 8px; text-align: left; }}
                th {{ background-color: #1ce783; color: #040405; }}
                tr:nth-child(even) {{ background-color: #f8f9fa; }}
                .footer {{ text-align: center; margin-top: 50px; font-size: 12px; color: #6c757d; }}
                .logo {{ text-align: center; margin-bottom: 20px; }}
            </style>
        </head>
        <body>
            <h1>PixelProbe Scan Report</h1>
            
            <h2>Report Information</h2>
            <table>
                <tr><th>Report ID</th><td>{report.report_id}</td></tr>
                <tr><th>Scan Type</th><td>{report.scan_type.replace('_', ' ').title()}</td></tr>
                <tr><th>Status</th><td>{report.status.title()}</td></tr>
                <tr><th>Start Time</th><td>{report.start_time.strftime('%Y-%m-%d %H:%M:%S UTC') if report.start_time else 'N/A'}</td></tr>
                <tr><th>End Time</th><td>{report.end_time.strftime('%Y-%m-%d %H:%M:%S UTC') if report.end_time else 'N/A'}</td></tr>
                <tr><th>Duration</th><td>{f"{int(report.duration_seconds // 60)}m {int(report.duration_seconds % 60)}s" if report.duration_seconds else 'N/A'}</td></tr>
            </table>
            
            <h2>Scan Statistics</h2>
            <table>
        """
        
        if report.scan_type in ['full_scan', 'rescan', 'deep_scan']:
            html_content += f"""
                <tr><th>Total Files Discovered</th><td>{report.total_files_discovered:,}</td></tr>
                <tr><th>Files Scanned</th><td>{report.files_scanned:,}</td></tr>
                <tr><th>New Files Added</th><td>{report.files_added:,}</td></tr>
                <tr><th>Files Updated</th><td>{report.files_updated:,}</td></tr>
                <tr><th>Corrupted Files</th><td>{report.files_corrupted:,}</td></tr>
                <tr><th>Files with Warnings</th><td>{report.files_with_warnings:,}</td></tr>
                <tr><th>Files with Errors</th><td>{report.files_error:,}</td></tr>
            """
        elif report.scan_type == 'cleanup':
            html_content += f"""
                <tr><th>Orphaned Records Found</th><td>{report.orphaned_records_found:,}</td></tr>
                <tr><th>Orphaned Records Deleted</th><td>{report.orphaned_records_deleted:,}</td></tr>
            """
        
        html_content += f"""
            </table>
            """
        
        # Add scanned files list for scan reports
        if report.scan_type in ['full_scan', 'rescan', 'deep_scan']:
            from models import ScanResult
            scanned_files = ScanResult.query.filter(
                ScanResult.scan_date >= report.start_time,
                ScanResult.scan_date <= (report.end_time or datetime.now(timezone.utc))
            ).order_by(ScanResult.file_path).limit(1000).all()
            
            html_content += f"""
                <h2>Scanned Files ({len(scanned_files)} files)</h2>
                <table>
                    <tr>
                        <th>File Path</th>
                        <th>Status</th>
                        <th>Size</th>
                        <th>Type</th>
                        <th>Scan Date</th>
                    </tr>
            """
            
            for file in scanned_files[:500]:  # Limit display to 500 files
                status = 'Corrupted' if file.is_corrupted and not file.marked_as_good else 'Healthy'
                size = f"{file.file_size / (1024*1024):.2f} MB" if file.file_size else 'N/A'
                file_type = file.file_type or 'Unknown'
                scan_date = file.scan_date.strftime('%Y-%m-%d %H:%M') if file.scan_date else 'N/A'
                status_class = 'corrupted' if status == 'Corrupted' else 'healthy'
                
                html_content += f"""
                    <tr>
                        <td style="max-width: 400px; overflow: hidden; text-overflow: ellipsis;">{file.file_path}</td>
                        <td><span class="{status_class}">{status}</span></td>
                        <td>{size}</td>
                        <td>{file_type}</td>
                        <td>{scan_date}</td>
                    </tr>
                """
            
            html_content += "</table>"
            
            if len(scanned_files) > 500:
                html_content += f"<p><em>Note: Showing first 500 of {len(scanned_files)} total files</em></p>"
        
        html_content += f"""
            <div class="footer">
                <p>Generated on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
                <p>This report is for compliance and auditing purposes</p>
            </div>
        </body>
        </html>
        """
        
        response = make_response(html_content)
        response.headers['Content-Type'] = 'text/html'
        
        return response
    
    except Exception as e:
        # Log any other errors (use module-level logger)
        logger.error(f"Failed to generate PDF report: {e}")
        return jsonify({'error': f'Failed to generate PDF: {str(e)}'}), 500

@reports_bp.route('/scan-reports/latest')
def get_latest_scan_reports():
    """Get the latest report for each scan type"""
    scan_types = ['full_scan', 'rescan', 'deep_scan', 'cleanup', 'file_changes']
    latest_reports = {}
    
    for scan_type in scan_types:
        report = ScanReport.query.filter_by(scan_type=scan_type)\
                                .order_by(ScanReport.start_time.desc())\
                                .first()
        
        if report:
            report_dict = report.to_dict()
            # Add formatted duration
            if report.duration_seconds:
                hours = int(report.duration_seconds // 3600)
                minutes = int((report.duration_seconds % 3600) // 60)
                seconds = int(report.duration_seconds % 60)
                
                if hours > 0:
                    report_dict['duration_formatted'] = f"{hours}h {minutes}m {seconds}s"
                elif minutes > 0:
                    report_dict['duration_formatted'] = f"{minutes}m {seconds}s"
                else:
                    report_dict['duration_formatted'] = f"{seconds}s"
            
            latest_reports[scan_type] = report_dict
    
    return jsonify(latest_reports)

@reports_bp.route('/scan-reports/<report_id>', methods=['DELETE'])
def delete_scan_report(report_id):
    """Delete a scan report by ID"""
    report = ScanReport.query.filter_by(report_id=report_id).first()
    if not report:
        return jsonify({'error': 'Report not found'}), 404
    
    try:
        db.session.delete(report)
        db.session.commit()
        return jsonify({'message': 'Report deleted successfully', 'report_id': report_id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to delete report: {str(e)}'}), 500

@reports_bp.route('/reports/download-multiple', methods=['POST'])
@validate_json_input({
    'filenames': {'required': True, 'type': list},
    'format': {'required': False, 'type': str}
})
def download_multiple_reports():
    """Download multiple reports as a zip file or combined PDF"""
    try:
        import io
        import zipfile
        from flask import current_app
        
        data = request.get_json()
        filenames = data.get('filenames', [])
        format_type = data.get('format', 'zip').lower()  # 'zip' or 'pdf'
        
        if not filenames:
            return jsonify({'error': 'No filenames provided'}), 400
        
        report_dir = os.path.join(current_app.instance_path, 'reports')
        
        # Validate all filenames
        for filename in filenames:
            if '..' in filename or '/' in filename or '\\' in filename:
                return jsonify({'error': f'Invalid filename: {filename}'}), 400
            if not filename.endswith('.json'):
                return jsonify({'error': f'Invalid report filename: {filename}'}), 400
        
        if format_type == 'pdf':
            # Generate combined PDF
            try:
                from reportlab.lib import colors
                from reportlab.lib.pagesizes import letter, landscape
                from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image
                from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
                from reportlab.lib.units import inch
                from reportlab.lib.enums import TA_CENTER
                
                # Create PDF buffer
                buffer = io.BytesIO()
                doc = SimpleDocTemplate(buffer, pagesize=landscape(letter))
                elements = []
                
                styles = getSampleStyleSheet()
                primary_green = colors.HexColor('#1ce783')
                primary_black = colors.HexColor('#040405')
                
                title_style = ParagraphStyle(
                    'CustomTitle',
                    parent=styles['Heading1'],
                    fontSize=24,
                    textColor=primary_black,
                    spaceAfter=30,
                    alignment=TA_CENTER
                )
                
                # Create styles for wrapping text
                cell_style = ParagraphStyle(
                    'CellStyle',
                    parent=styles['Normal'],
                    fontSize=6,
                    leading=7
                )
                
                # Add logo at the top of the first page
                logo_path = os.path.join(os.path.dirname(__file__), '../../static/images/pixelprobe-logo.png')
                if os.path.exists(logo_path):
                    # Maintain aspect ratio for square logo (670x729 pixels)
                    logo = Image(logo_path, width=1.5*inch, height=1.5*inch, kind='proportional')
                    logo.hAlign = 'CENTER'
                    elements.append(logo)
                    elements.append(Spacer(1, 0.3*inch))
                
                # Add each report
                for idx, filename in enumerate(filenames):
                    file_path = os.path.join(report_dir, filename)
                    if not os.path.exists(file_path):
                        continue
                    
                    with open(file_path, 'r') as f:
                        report_data = json.load(f)
                    
                    # Add page break between reports
                    if idx > 0:
                        elements.append(PageBreak())
                    
                    # Report title
                    report_type = 'Cleanup Report' if filename.startswith('cleanup_report_') else 'Scan Report'
                    elements.append(Paragraph(f"PixelProbe {report_type}", title_style))
                    elements.append(Spacer(1, 0.2*inch))
                    
                    # Report info
                    info_text = f"Report ID: {filename}<br/>"
                    info_text += f"Status: {report_data.get('status', 'Unknown')}<br/>"
                    info_text += f"Start Time: {report_data.get('start_time', 'N/A')}<br/>"
                    info_text += f"End Time: {report_data.get('end_time', 'N/A')}<br/>"
                    elements.append(Paragraph(info_text, styles['Normal']))
                    elements.append(Spacer(1, 0.2*inch))
                    
                    # Statistics
                    if 'stats' in report_data:
                        elements.append(Paragraph("Statistics", styles['Heading2']))
                        stats_data = [['Metric', 'Value']]
                        for key, value in report_data['stats'].items():
                            stats_data.append([key.replace('_', ' ').title(), str(value)])
                        
                        stats_table = Table(stats_data)
                        stats_table.setStyle(TableStyle([
                            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                            ('BACKGROUND', (0, 0), (-1, 0), primary_green),
                            ('TEXTCOLOR', (0, 0), (-1, 0), primary_black),
                        ]))
                        elements.append(stats_table)
                        elements.append(Spacer(1, 0.2*inch))
                    
                    # Add scanned files if available
                    if 'results' in report_data and report_data['results']:
                        results = report_data['results']
                        
                        # Scanned files list
                        if 'scanned_files' in results and results['scanned_files']:
                            elements.append(Paragraph("Scanned Files", styles['Heading2']))
                            elements.append(Spacer(1, 0.1*inch))
                            
                            # Limit files shown in PDF to 500
                            files_to_show = results['scanned_files'][:500]
                            
                            # Create files table with all required fields
                            files_data = [['Status', 'File Path', 'Size', 'Type', 'Tool', 'Details', 'Scan Date']]
                            for file in files_to_show:
                                status = 'Corrupted' if file.get('is_corrupted') else 'Healthy'
                                if file.get('has_warnings'):
                                    status = 'Warning'
                                
                                file_path = file.get('file_path', '')
                                # Wrap file path in Paragraph for proper text wrapping
                                file_path_para = Paragraph(file_path, cell_style)
                                
                                file_size = file.get('file_size', 0)
                                if file_size:
                                    size_mb = file_size / (1024 * 1024)
                                    size_str = f"{size_mb:.1f} MB"
                                else:
                                    size_str = 'N/A'
                                
                                file_type = file.get('file_type') or 'N/A'
                                scan_tool = file.get('scan_tool') or 'N/A'
                                
                                # Get details
                                details = ''
                                if file.get('is_corrupted') and file.get('corruption_details'):
                                    details = str(file['corruption_details'])
                                elif file.get('has_warnings') and file.get('warning_details'):
                                    details = str(file['warning_details'])
                                
                                # Wrap details in Paragraph
                                details_para = Paragraph(details, cell_style)
                                
                                scan_date = file.get('scan_date', 'N/A')
                                
                                files_data.append([
                                    status,
                                    file_path_para,
                                    size_str,
                                    file_type,
                                    scan_tool,
                                    details_para,
                                    scan_date
                                ])
                            
                            # Create table with proper column widths
                            table = Table(files_data, colWidths=[0.7*inch, 3.5*inch, 0.7*inch, 0.8*inch, 0.6*inch, 2.5*inch, 1.2*inch])
                            table.setStyle(TableStyle([
                                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                                ('FONTSIZE', (0, 0), (-1, -1), 8),
                                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                                ('BACKGROUND', (0, 0), (-1, 0), primary_green),
                                ('TEXTCOLOR', (0, 0), (-1, 0), primary_black),
                                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                                ('PADDING', (0, 0), (-1, -1), 4),
                            ]))
                            
                            elements.append(table)
                            
                            if len(results['scanned_files']) > 500:
                                elements.append(Spacer(1, 0.1*inch))
                                elements.append(Paragraph("Note: Showing first 500 results", styles['Normal']))
                
                # Build PDF
                doc.build(elements)
                pdf_data = buffer.getvalue()
                buffer.close()
                
                # Create response
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                response = make_response(pdf_data)
                response.headers['Content-Type'] = 'application/pdf'
                response.headers['Content-Disposition'] = f'attachment; filename=pixelprobe_reports_{timestamp}.pdf'
                
                return response
                
            except ImportError:
                logger.error("reportlab not installed for PDF export")
                return jsonify({'error': 'PDF export requires reportlab package'}), 500
        
        else:  # ZIP format
            # Create zip buffer
            buffer = io.BytesIO()
            
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for filename in filenames:
                    file_path = os.path.join(report_dir, filename)
                    if os.path.exists(file_path):
                        zipf.write(file_path, filename)
            
            buffer.seek(0)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            response = make_response(buffer.getvalue())
            response.headers['Content-Type'] = 'application/zip'
            response.headers['Content-Disposition'] = f'attachment; filename=pixelprobe_reports_{timestamp}.zip'
            
            return response
            
    except Exception as e:
        logger.error(f"Error downloading multiple reports: {str(e)}")
        return jsonify({'error': str(e)}), 500