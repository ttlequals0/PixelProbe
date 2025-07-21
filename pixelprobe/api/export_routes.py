from flask import Blueprint, request, jsonify, send_file, Response
import os
import csv
import io
import logging
from datetime import datetime

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
    """Export scan results to CSV"""
    try:
        # Determine export type and get appropriate results
        if request.method == 'POST':
            data = request.get_json() or {}
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
                logger.info(f"Exporting {len(results)} scan results to CSV (filter: {filter_type}, search: '{search}')")
        else:
            # GET request - export all files
            results = ScanResult.query.all()
            export_type = "all"
            logger.info(f"Exporting {len(results)} scan results to CSV (all results via GET)")
        
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
            'Corruption Details',
            'Scan Date',
            'Scan Status',
            'Discovered Date',
            'Marked as Good'
        ])
        
        # Write data rows
        for result in results:
            writer.writerow([
                result.id,
                result.file_path,
                result.file_size or 0,
                result.file_type or 'Unknown',
                result.creation_date.isoformat() if result.creation_date else '',
                'Yes' if result.is_corrupted else 'No',
                result.corruption_details or '',
                result.scan_date.isoformat() if result.scan_date else '',
                getattr(result, 'scan_status', 'completed'),  # Default to completed for old records
                getattr(result, 'discovered_date', result.scan_date).isoformat() if getattr(result, 'discovered_date', result.scan_date) else '',
                'Yes' if result.marked_as_good else 'No'
            ])
        
        # Prepare response
        output.seek(0)
        csv_content = output.getvalue()
        output.close()
        
        # Create filename with timestamp and export type
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
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
        logger.error(f"Error exporting CSV: {str(e)}")
        return jsonify({'error': f'Export failed: {str(e)}'}), 500