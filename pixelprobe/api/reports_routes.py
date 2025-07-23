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
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT
        
        # Create PDF buffer
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
        
        # Container for the 'Flowable' objects
        elements = []
        
        # Define styles
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#2c3e50'),
            spaceAfter=30,
            alignment=TA_CENTER
        )
        
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=16,
            textColor=colors.HexColor('#34495e'),
            spaceAfter=12
        )
        
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
            dirs = json.loads(report.directories_scanned)
            report_info.append(['Directories:', ', '.join(dirs[:3]) + ('...' if len(dirs) > 3 else '')])
        
        info_table = Table(report_info, colWidths=[2*inch, 4*inch])
        info_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
            ('ALIGN', (1, 0), (1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#ecf0f1')),
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
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3498db')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#ecf0f1')]),
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
        
    except ImportError:
        # If reportlab is not installed, return a simple HTML version that can be printed to PDF
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Scan Report {report.report_id}</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; }}
                h1 {{ color: #2c3e50; text-align: center; }}
                h2 {{ color: #34495e; margin-top: 30px; }}
                table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #3498db; color: white; }}
                tr:nth-child(even) {{ background-color: #f2f2f2; }}
                .footer {{ text-align: center; margin-top: 50px; font-size: 12px; color: #7f8c8d; }}
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