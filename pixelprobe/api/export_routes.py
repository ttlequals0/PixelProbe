from flask import Blueprint, request, send_file, Response, make_response, stream_with_context
import os
import csv
import io
import json
import logging
import magic
from datetime import datetime, timezone
from xml.sax.saxutils import escape as escape_xml

from pixelprobe.models import db, ScanResult
from pixelprobe.auth import auth_required
from pixelprobe.utils.security import PathTraversalError, open_authorized_media_file

logger = logging.getLogger(__name__)

export_bp = Blueprint('export', __name__, url_prefix='/api')

_INLINE_MEDIA_MIMETYPES = {
    'audio/mpeg', 'audio/ogg', 'audio/wav', 'audio/webm',
    'image/avif', 'image/gif', 'image/jpeg', 'image/png', 'image/webp',
    'video/mp4', 'video/ogg', 'video/quicktime', 'video/webm',
}

_MIME_ALIASES = {
    'audio/x-wav': 'audio/wav',
}


def _preview_mimetype(media_file):
    try:
        media_file.seek(0)
        sample = media_file.read(4096)
        try:
            mime_type = magic.from_buffer(sample, mime=True).lower()
        except Exception:
            return None
    finally:
        media_file.seek(0)
    mime_type = _MIME_ALIASES.get(mime_type, mime_type)
    return mime_type if mime_type in _INLINE_MEDIA_MIMETYPES else None

def _result_status(result):
    if result.scan_status in ('error', 'failed'):
        return 'Error'
    if result.scan_status == 'unreadable':
        return 'Unreadable'
    if result.scan_status == 'pending':
        return 'Pending'
    if result.scan_status == 'scanning':
        return 'Scanning'
    if result.scan_status in ('skipped', 'unsupported'):
        return 'Skipped'
    if result.scan_status != 'completed':
        return 'Unknown'
    if result.scan_tool == 'error':
        return 'Error'
    if result.scan_tool == 'unsupported':
        return 'Skipped'
    if result.is_corrupted and not result.marked_as_good:
        return 'Corrupted'
    if getattr(result, 'has_warnings', False) and not result.marked_as_good:
        return 'Warning'
    return 'Healthy'


def _apply_status_filter(query, filter_type):
    completed = ScanResult.scan_status == 'completed'
    completed_normal = db.and_(
        completed,
        db.or_(ScanResult.scan_tool == None,
               ScanResult.scan_tool.notin_(('error', 'unsupported'))),
    )
    unmarked = db.or_(ScanResult.marked_as_good == False, ScanResult.marked_as_good == None)
    clean = db.and_(
        db.or_(ScanResult.is_corrupted == False, ScanResult.is_corrupted == None),
        db.or_(ScanResult.has_warnings == False, ScanResult.has_warnings == None),
    )
    if filter_type == 'corrupted':
        return query.filter(completed_normal, ScanResult.is_corrupted == True, unmarked)
    if filter_type == 'healthy':
        return query.filter(completed_normal, db.or_(clean, ScanResult.marked_as_good == True))
    if filter_type == 'warning':
        return query.filter(completed_normal, ScanResult.has_warnings == True,
                            db.or_(ScanResult.is_corrupted == False,
                                   ScanResult.is_corrupted == None), unmarked)
    if filter_type == 'pending':
        return query.filter(ScanResult.scan_status == 'pending')
    if filter_type == 'error':
        return query.filter(db.or_(
            ScanResult.scan_status.in_(('error', 'failed', 'unreadable')),
            db.and_(completed, ScanResult.scan_tool == 'error'),
        ))
    return query


def _pdf_export_rows(query):
    """Load only the fields rendered by the bounded generic PDF export."""
    return (query.with_entities(
        ScanResult.id, ScanResult.file_path, ScanResult.file_size,
        ScanResult.file_type, ScanResult.is_corrupted, ScanResult.marked_as_good,
        ScanResult.has_warnings, ScanResult.scan_status, ScanResult.scan_tool,
        ScanResult.corruption_details, ScanResult.warning_details,
        ScanResult.error_message, ScanResult.scan_date,
    ).order_by(ScanResult.id).limit(500).all())

@export_bp.route('/view/<int:result_id>', methods=['GET', 'OPTIONS'])
@auth_required
def view_file(result_id):
    """View/stream a media file"""
    # Handle OPTIONS request for CORS preflight
    if request.method == 'OPTIONS':
        response = make_response({'status': 'ok'})
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Range, Content-Type'
        response.headers['Access-Control-Max-Age'] = '3600'
        return response
    
    result = db.get_or_404(ScanResult, result_id)
    
    logger.info(f"View requested for file: {result.file_path} (ID: {result_id})")
    
    try:
        media_file, _, file_stat = open_authorized_media_file(result.file_path)
    except PathTraversalError:
        logger.warning("View denied for unavailable media result %s", result_id)
        return {'error': 'File not found'}, 404
    
    file_size = file_stat.st_size
    logger.info(f"Serving file for viewing: {result.file_path}")
    response = None
    try:
        preview_type = _preview_mimetype(media_file)
        inline_media = preview_type is not None
        response = send_file(
            media_file,
            as_attachment=not inline_media,
            mimetype=preview_type if inline_media else 'application/octet-stream',
            download_name=os.path.basename(result.file_path),
            conditional=False,
        )
        response.content_length = file_size
        response.make_conditional(request.environ, accept_ranges=True, complete_length=file_size)
    except Exception:
        if response is not None:
            response.close()
        else:
            media_file.close()
        raise
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # Add CORS headers for mobile compatibility
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Range'
    return response

@export_bp.route('/download/<int:result_id>')
@auth_required
def download_file(result_id):
    """Download a media file"""
    result = db.get_or_404(ScanResult, result_id)
    
    logger.info(f"Download requested for file: {result.file_path} (ID: {result_id})")
    
    try:
        media_file, _, _ = open_authorized_media_file(result.file_path)
    except PathTraversalError:
        logger.warning("Download denied for unavailable media result %s", result_id)
        return {'error': 'File not found'}, 404
    
    logger.info(f"Starting download of file: {result.file_path}")
    return send_file(media_file, as_attachment=True, download_name=os.path.basename(result.file_path))

@export_bp.route('/export', methods=['GET', 'POST'])
@auth_required
def export_scan_results():
    """Export scan results to CSV, JSON, or PDF
    
    GET Parameters:
        format: Output format ('csv', 'json', 'pdf') - defaults to 'csv'
        filter: Filter type ('all', 'corrupted', 'healthy', 'pending')
        search: Search term to filter files
    
    POST Body:
        format: Output format ('csv', 'json', 'pdf')
        filter: Filter type ('all', 'corrupted', 'healthy', 'pending')
        search: Search term
        file_ids: Array of specific file IDs to export
    """
    try:
        # Determine export format and get appropriate results
        format_type = 'csv'  # Default format
        
        if request.method == 'POST':
            data = request.get_json() or {}
            format_type = data.get('format', 'csv').lower()
            file_ids = data.get('file_ids', [])
            
            if file_ids:
                # Export selected files
                query = ScanResult.query.filter(ScanResult.id.in_(file_ids))
                result_total = query.count()
                results = _pdf_export_rows(query) if format_type == 'pdf' else None
                export_type = "selected"
                logger.info("Exporting %s selected scan results", result_total)
            else:
                # Export based on current filter and search
                filter_type = data.get('filter', 'all')
                search = data.get('search', '')
                
                query = ScanResult.query
                
                # Apply search filter
                if search:
                    query = query.filter(ScanResult.file_path.contains(search))
                
                query = _apply_status_filter(query, filter_type)
                
                result_total = query.count()
                results = _pdf_export_rows(query) if format_type == 'pdf' else None
                export_type = filter_type if filter_type != 'all' else 'all'
                logger.info("Exporting %s scan results to %s", result_total, format_type.upper())
        else:
            # GET request - support format, filter, and search parameters
            format_type = request.args.get('format', 'csv').lower()
            filter_type = request.args.get('filter', 'all')
            search = request.args.get('search', '')
            
            # Validate format
            if format_type not in ['csv', 'json', 'pdf']:
                format_type = 'csv'
            
            # Build query based on filter
            query = ScanResult.query
            
            # Apply search filter if provided
            if search:
                query = query.filter(ScanResult.file_path.contains(search))
            
            query = _apply_status_filter(query, filter_type)
            
            result_total = query.count()
            results = _pdf_export_rows(query) if format_type == 'pdf' else None
            export_type = filter_type if filter_type != 'all' else 'all'
            logger.info("Exporting %s scan results to %s", result_total, format_type.upper())
        
        def result_batches():
            columns = (ScanResult.id, ScanResult.file_path, ScanResult.file_size,
                       ScanResult.file_type, ScanResult.creation_date, ScanResult.is_corrupted,
                       ScanResult.corruption_details, ScanResult.scan_date, ScanResult.scan_status,
                       ScanResult.discovered_date, ScanResult.marked_as_good, ScanResult.has_warnings,
                       ScanResult.warning_details, ScanResult.error_message)
            last_id = 0
            while True:
                session = db.session.session_factory()
                try:
                    batch = (query.with_session(session).filter(
                        ScanResult.id > last_id, ScanResult.id <= export_max_id)
                             .order_by(ScanResult.id).with_entities(*columns).limit(500).all())
                finally:
                    session.close()
                if not batch:
                    return
                last_id = batch[-1].id
                yield from batch

        export_max_id = query.with_entities(db.func.max(ScanResult.id)).scalar() or 0

        def release_request_session():
            db.session.rollback()

        # Create filename with timestamp and export type
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        
        # Handle different export formats
        if format_type == 'json':
            # Export as JSON
            def generate_json():
                yield '[\n'
                first = True
                for result in result_batches():
                    if not first: yield ',\n'
                    first = False
                    yield json.dumps({
                    'id': result.id,
                    'file_path': result.file_path,
                    'file_size': result.file_size or 0,
                    'file_type': result.file_type or 'Unknown',
                    'creation_date': result.creation_date.isoformat() if result.creation_date else None,
                    'is_corrupted': result.is_corrupted,
                    'corruption_details': result.corruption_details,
                    'scan_date': result.scan_date.isoformat() if result.scan_date else None,
                    'scan_status': getattr(result, 'scan_status', 'completed'),
                    'discovered_date': getattr(result, 'discovered_date', result.scan_date).isoformat() if getattr(result, 'discovered_date', result.scan_date) else None,
                    'marked_as_good': result.marked_as_good,
                    'has_warnings': getattr(result, 'has_warnings', False),
                    'warning_details': getattr(result, 'warning_details', None),
                    'error_message': getattr(result, 'error_message', None),
                    'details': {
                        'corruption': result.corruption_details,
                        'warning': getattr(result, 'warning_details', None),
                        'error': getattr(result, 'error_message', None)
                    }
                    }, default=str)
                yield '\n]\n'
            filename = f"pixelprobe_{export_type}_{timestamp}.json"
            release_request_session()
            return Response(stream_with_context(generate_json()), mimetype='application/json', headers={'Content-Disposition': f'attachment; filename={filename}'})
            
        elif format_type == 'pdf':
            # Export as PDF
            try:
                from reportlab.lib import colors
                from reportlab.lib.pagesizes import letter, landscape
                from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
                from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
                from reportlab.lib.units import inch
                from reportlab.lib.enums import TA_CENTER
                
                # Create PDF buffer
                buffer = io.BytesIO()
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
                
                # Add title
                elements.append(Paragraph("PixelProbe Scan Results Export", title_style))
                elements.append(Spacer(1, 0.2*inch))
                
                # Add export info
                info_text = f"Export Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}<br/>"
                info_text += f"Total Records: {result_total}<br/>"
                info_text += f"Filter: {escape_xml(str(export_type))}"
                elements.append(Paragraph(info_text, styles['Normal']))
                elements.append(Spacer(1, 0.2*inch))
                
                # Create table data
                table_data = [['File Path', 'Status', 'Size', 'Type', 'Details', 'Scan Date']]
                
                for result in results[:500]:  # Limit to 500 for PDF size
                    status = _result_status(result)
                    
                    size = f"{result.file_size / (1024*1024):.2f} MB" if result.file_size else 'N/A'
                    file_type = result.file_type or 'Unknown'
                    scan_date = result.scan_date.strftime('%Y-%m-%d %H:%M') if result.scan_date else 'N/A'
                    
                    # Don't truncate file paths - use Paragraph for wrapping
                    file_path = result.file_path
                    
                    # Combine details for display
                    details = []
                    if result.corruption_details:
                        details.append(result.corruption_details[:40] + "..." if len(result.corruption_details) > 40 else result.corruption_details)
                    if getattr(result, 'warning_details', None):
                        warning = getattr(result, 'warning_details', '')
                        details.append(warning[:40] + "..." if len(warning) > 40 else warning)
                    if getattr(result, 'error_message', None):
                        error = getattr(result, 'error_message', '')
                        details.append(error[:40] + "..." if len(error) > 40 else error)
                    details_text = "; ".join(details) if details else ''
                    
                    # Wrap file path and details in Paragraph for proper text wrapping
                    file_path_para = Paragraph(escape_xml(str(file_path)), cell_style)
                    details_para = Paragraph(escape_xml(str(details_text)), cell_style)
                    
                    table_data.append([
                        file_path_para,
                        Paragraph(escape_xml(str(status)), cell_style),
                        size,
                        Paragraph(escape_xml(str(file_type)), cell_style),
                        details_para,
                        scan_date
                    ])
                
                # Create table - adjusted column widths to fit details
                table = Table(table_data, colWidths=[3.5*inch, 0.7*inch, 0.7*inch, 1*inch, 2.5*inch, 1.1*inch])
                table.setStyle(TableStyle([
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                    ('FONTSIZE', (0, 0), (-1, -1), 8),
                    ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                    ('BACKGROUND', (0, 0), (-1, 0), primary_green),
                    ('TEXTCOLOR', (0, 0), (-1, 0), primary_black),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                    ('PADDING', (0, 0), (-1, -1), 4),
                ]))
                
                elements.append(table)
                
                if result_total > len(results):
                    elements.append(Spacer(1, 0.1*inch))
                    elements.append(Paragraph(
                        f"Note: Showing first {len(results)} of {result_total} total records. "
                        "Use CSV or JSON export for the complete result set.", styles['Normal']))
                
                # Build PDF
                doc.build(elements)
                
                # Get PDF data
                pdf_data = buffer.getvalue()
                buffer.close()
                
                filename = f"pixelprobe_{export_type}_{timestamp}.pdf"
                logger.info(f"PDF export completed - {len(results)} records exported to {filename}")
                
                return send_file(
                    io.BytesIO(pdf_data),
                    mimetype='application/pdf',
                    as_attachment=True,
                    download_name=filename
                )
                
            except ImportError:
                logger.error("reportlab not installed for PDF export")
                return {'error': 'PDF export requires reportlab package'}, 500
                
        else:
            # Default to CSV export
            def generate_csv():
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow([
                'ID',
                'File Path',
                'File Size (bytes)',
                'File Type',
                'Creation Date',
                'Is Corrupted',
                'Has Warnings',
                'Details',
                'Scan Date',
                'Scan Status',
                'Discovered Date',
                'Marked as Good'
                ])
                yield output.getvalue(); output.seek(0); output.truncate(0)
                for result in result_batches():
                    details = []
                    if result.corruption_details:
                        details.append(f"Corruption: {result.corruption_details}")
                    if result.warning_details:
                        details.append(f"Warning: {result.warning_details}")
                    if result.error_message:
                        details.append(f"Error: {result.error_message}")
                    details_text = "; ".join(details) if details else ''
                
                    writer.writerow([
                    result.id,
                    result.file_path,
                    result.file_size or 0,
                    result.file_type or 'Unknown',
                    result.creation_date.isoformat() if result.creation_date else '',
                    'Yes' if result.is_corrupted else 'No',
                    'Yes' if getattr(result, 'has_warnings', False) else 'No',
                    details_text,
                    result.scan_date.isoformat() if result.scan_date else '',
                    getattr(result, 'scan_status', 'completed'),  # Default to completed for old records
                    getattr(result, 'discovered_date', result.scan_date).isoformat() if getattr(result, 'discovered_date', result.scan_date) else '',
                    'Yes' if result.marked_as_good else 'No'
                    ])
                    yield output.getvalue(); output.seek(0); output.truncate(0)
            filename = f"pixelprobe_{export_type}_{timestamp}.csv"
            release_request_session()
            return Response(stream_with_context(generate_csv()), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename={filename}'})
        
    except Exception as e:
        logger.error(f"Error exporting: {str(e)}", exc_info=True)
        return {'error': 'Export failed'}, 500
