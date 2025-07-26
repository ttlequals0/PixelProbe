from flask import Blueprint, request, jsonify, send_file, Response, make_response
import os
import csv
import io
import json
import logging
from datetime import datetime, timezone

from models import db, ScanResult

logger = logging.getLogger(__name__)

export_bp = Blueprint('export', __name__, url_prefix='/api')

@export_bp.route('/view/<int:result_id>', methods=['GET', 'OPTIONS'])
def view_file(result_id):
    """View/stream a media file"""
    # Handle OPTIONS request for CORS preflight
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Range, Content-Type'
        response.headers['Access-Control-Max-Age'] = '3600'
        return response
    
    result = ScanResult.query.get_or_404(result_id)
    
    logger.info(f"View requested for file: {result.file_path} (ID: {result_id})")
    
    if not os.path.exists(result.file_path):
        logger.error(f"View failed - file not found: {result.file_path}")
        return jsonify({'error': 'File not found'}), 404
    
    # Get file stats
    file_size = os.path.getsize(result.file_path)
    file_type = result.file_type or 'application/octet-stream'
    
    # Handle range requests for video streaming (required for mobile)
    range_header = request.headers.get('range')
    if range_header and file_type.startswith('video/'):
        logger.info(f"Range request for video: {range_header}")
        
        # Parse range header
        try:
            byte_start = int(range_header.split('=')[1].split('-')[0])
            byte_end = file_size - 1
            if '-' in range_header.split('=')[1] and range_header.split('=')[1].split('-')[1]:
                byte_end = int(range_header.split('=')[1].split('-')[1])
            
            # Limit chunk size for mobile
            max_chunk = 1024 * 1024  # 1MB chunks
            if byte_end - byte_start > max_chunk:
                byte_end = byte_start + max_chunk
            
            logger.info(f"Serving bytes {byte_start}-{byte_end}/{file_size}")
            
            def generate():
                with open(result.file_path, 'rb') as f:
                    f.seek(byte_start)
                    remaining = byte_end - byte_start + 1
                    while remaining:
                        chunk_size = min(8192, remaining)
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
            
            response = Response(
                generate(),
                206,  # Partial Content
                mimetype=file_type,
                headers={
                    'Content-Range': f'bytes {byte_start}-{byte_end}/{file_size}',
                    'Accept-Ranges': 'bytes',
                    'Content-Length': str(byte_end - byte_start + 1),
                    'Cache-Control': 'no-cache',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Methods': 'GET, OPTIONS',
                    'Access-Control-Allow-Headers': 'Range'
                }
            )
            return response
            
        except Exception as e:
            logger.error(f"Error handling range request: {e}")
            # Fall through to regular response
    
    # Regular response for non-range requests
    logger.info(f"Serving file for viewing: {result.file_path}")
    response = send_file(result.file_path, as_attachment=False, mimetype=file_type)
    response.headers['Accept-Ranges'] = 'bytes'
    response.headers['Cache-Control'] = 'no-cache'
    # Add CORS headers for mobile compatibility
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Range'
    return response

@export_bp.route('/download/<int:result_id>')
def download_file(result_id):
    """Download a media file"""
    result = ScanResult.query.get_or_404(result_id)
    
    logger.info(f"Download requested for file: {result.file_path} (ID: {result_id})")
    
    if not os.path.exists(result.file_path):
        logger.error(f"Download failed - file not found: {result.file_path}")
        return jsonify({'error': 'File not found'}), 404
    
    logger.info(f"Starting download of file: {result.file_path}")
    return send_file(result.file_path, as_attachment=True)

@export_bp.route('/export-csv', methods=['GET', 'POST'])
def export_csv():
    """Export scan results to CSV, JSON, or PDF"""
    try:
        # Determine export format and get appropriate results
        format_type = 'csv'  # Default format
        if request.method == 'POST':
            data = request.get_json() or {}
            format_type = data.get('format', 'csv').lower()
            file_ids = data.get('file_ids', [])
            
            if file_ids:
                # Export selected files
                results = ScanResult.query.filter(ScanResult.id.in_(file_ids)).all()
                export_type = "selected"
                logger.info(f"Exporting {len(results)} selected scan results to CSV")
            else:
                # Export based on current filter and search
                filter_type = data.get('filter', 'all')
                search = data.get('search', '')
                
                query = ScanResult.query
                
                # Apply search filter
                if search:
                    query = query.filter(ScanResult.file_path.contains(search))
                
                # Apply corruption filter
                if filter_type == 'corrupted':
                    # Show only corrupted files that don't have warnings and aren't marked as good
                    query = query.filter(
                        (ScanResult.is_corrupted == True) & 
                        ((ScanResult.has_warnings == False) | (ScanResult.has_warnings == None)) &
                        (ScanResult.marked_as_good == False)
                    )
                elif filter_type == 'healthy':
                    # Show only healthy files (no corruption, no warnings, or marked as good)
                    query = query.filter(
                        ((ScanResult.is_corrupted == False) & 
                         ((ScanResult.has_warnings == False) | (ScanResult.has_warnings == None))) |
                        (ScanResult.marked_as_good == True)
                    )
                elif filter_type == 'warning':
                    # Show files with warnings that aren't marked as good
                    query = query.filter(
                        (ScanResult.has_warnings == True) &
                        (ScanResult.marked_as_good == False)
                    )
                # 'all' filter - no additional filtering needed
                
                results = query.all()
                export_type = filter_type if filter_type != 'all' else 'all'
                logger.info(f"Exporting {len(results)} scan results to {format_type.upper()} (filter: {filter_type}, search: '{search}')")
        else:
            # GET request - export all files (CSV by default)
            results = ScanResult.query.all()
            export_type = "all"
            logger.info(f"Exporting {len(results)} scan results to {format_type.upper()} (all results via GET)")
        
        # Create filename with timestamp and export type
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Handle different export formats
        if format_type == 'json':
            # Export as JSON
            json_data = []
            for result in results:
                json_data.append({
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
                })
            
            json_content = json.dumps(json_data, indent=2)
            filename = f"pixelprobe_{export_type}_{timestamp}.json"
            
            logger.info(f"JSON export completed - {len(results)} records exported to {filename}")
            
            return send_file(
                io.BytesIO(json_content.encode('utf-8')),
                mimetype='application/json',
                as_attachment=True,
                download_name=filename
            )
            
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
                info_text += f"Total Records: {len(results)}<br/>"
                info_text += f"Filter: {export_type}"
                elements.append(Paragraph(info_text, styles['Normal']))
                elements.append(Spacer(1, 0.2*inch))
                
                # Create table data
                table_data = [['File Path', 'Status', 'Size', 'Type', 'Details', 'Scan Date']]
                
                for result in results[:500]:  # Limit to 500 for PDF size
                    status = 'Corrupted' if result.is_corrupted and not result.marked_as_good else 'Healthy'
                    if getattr(result, 'has_warnings', False) and not result.marked_as_good:
                        status = 'Warning'
                    
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
                    file_path_para = Paragraph(file_path, cell_style)
                    details_para = Paragraph(details_text, cell_style)
                    
                    table_data.append([
                        file_path_para,
                        status,
                        size,
                        file_type,
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
                
                if len(results) > 500:
                    elements.append(Spacer(1, 0.1*inch))
                    elements.append(Paragraph(f"Note: Showing first 500 of {len(results)} total records", styles['Normal']))
                
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
                return jsonify({'error': 'PDF export requires reportlab package'}), 500
                
        else:
            # Default to CSV export
            # Create CSV in memory
            output = io.StringIO()
            writer = csv.writer(output)
            
            # Write CSV header
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
            
            # Write data rows
            for result in results:
                # Combine all details into one column
                details = []
                if result.corruption_details:
                    details.append(f"Corruption: {result.corruption_details}")
                if getattr(result, 'warning_details', None):
                    details.append(f"Warning: {result.warning_details}")
                if getattr(result, 'error_message', None):
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
            
            # Prepare response
            output.seek(0)
            csv_content = output.getvalue()
            output.close()
            
            filename = f"pixelprobe_{export_type}_{timestamp}.csv"
            
            logger.info(f"CSV export completed - {len(results)} records exported to {filename}")
            
            # Return CSV file
            return send_file(
                io.BytesIO(csv_content.encode('utf-8')),
                mimetype='text/csv',
                as_attachment=True,
                download_name=filename
            )
        
    except Exception as e:
        logger.error(f"Error exporting: {str(e)}")
        return jsonify({'error': f'Export failed: {str(e)}'}), 500