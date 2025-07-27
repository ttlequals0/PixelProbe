"""
Export service for handling data exports
"""

import os
import csv
import io
import logging
from datetime import datetime
from typing import List, Dict, Optional
from flask import send_file, Response

from models import db, ScanResult

logger = logging.getLogger(__name__)

class ExportService:
    """Service for exporting scan results"""
    
    def export_to_csv(self, filter_type: str = 'all', search: str = '', 
                     file_ids: Optional[List[int]] = None) -> io.BytesIO:
        """Export scan results to CSV format"""
        try:
            # Get results based on criteria
            if file_ids:
                results = ScanResult.query.filter(ScanResult.id.in_(file_ids)).all()
                export_type = "selected"
            else:
                query = ScanResult.query
                
                # Apply search filter
                if search:
                    query = query.filter(ScanResult.file_path.contains(search))
                
                # Apply corruption filter
                if filter_type == 'corrupted':
                    query = query.filter(
                        (ScanResult.is_corrupted == True) & 
                        ((ScanResult.has_warnings == False) | (ScanResult.has_warnings == None)) &
                        (ScanResult.marked_as_good == False)
                    )
                elif filter_type == 'healthy':
                    query = query.filter(
                        ((ScanResult.is_corrupted == False) & 
                         ((ScanResult.has_warnings == False) | (ScanResult.has_warnings == None))) |
                        (ScanResult.marked_as_good == True)
                    )
                elif filter_type == 'warning':
                    query = query.filter(
                        (ScanResult.has_warnings == True) &
                        (ScanResult.marked_as_good == False)
                    )
                
                results = query.all()
                export_type = filter_type if filter_type != 'all' else 'all'
            
            logger.info(f"Exporting {len(results)} scan results to CSV (type: {export_type})")
            
            # Create CSV
            output = io.StringIO()
            writer = csv.writer(output)
            
            # Write header
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
                    getattr(result, 'scan_status', 'completed'),
                    getattr(result, 'discovered_date', result.scan_date).isoformat() 
                        if getattr(result, 'discovered_date', result.scan_date) else '',
                    'Yes' if result.marked_as_good else 'No'
                ])
            
            # Prepare response
            output.seek(0)
            csv_content = output.getvalue()
            output.close()
            
            # Convert to bytes
            return io.BytesIO(csv_content.encode('utf-8'))
            
        except Exception as e:
            logger.error(f"Error exporting to CSV: {e}")
            raise
    
    def export_to_json(self, filter_type: str = 'all', search: str = '',
                      file_ids: Optional[List[int]] = None) -> Dict:
        """Export scan results to JSON format"""
        try:
            # Get results based on criteria (similar to CSV export)
            if file_ids:
                results = ScanResult.query.filter(ScanResult.id.in_(file_ids)).all()
            else:
                query = ScanResult.query
                
                if search:
                    query = query.filter(ScanResult.file_path.contains(search))
                
                if filter_type == 'corrupted':
                    query = query.filter(
                        (ScanResult.is_corrupted == True) & 
                        (ScanResult.marked_as_good == False)
                    )
                elif filter_type == 'healthy':
                    query = query.filter(
                        (ScanResult.is_corrupted == False) | 
                        (ScanResult.marked_as_good == True)
                    )
                
                results = query.all()
            
            # Convert to JSON-serializable format
            export_data = {
                'export_date': datetime.now().isoformat(),
                'filter_type': filter_type,
                'search_term': search,
                'total_records': len(results),
                'results': []
            }
            
            for result in results:
                export_data['results'].append({
                    'id': result.id,
                    'file_path': result.file_path,
                    'file_size': result.file_size,
                    'file_type': result.file_type,
                    'creation_date': result.creation_date.isoformat() if result.creation_date else None,
                    'is_corrupted': result.is_corrupted,
                    'corruption_details': result.corruption_details,
                    'scan_date': result.scan_date.isoformat() if result.scan_date else None,
                    'scan_status': getattr(result, 'scan_status', 'completed'),
                    'marked_as_good': result.marked_as_good
                })
            
            return export_data
            
        except Exception as e:
            logger.error(f"Error exporting to JSON: {e}")
            raise
    
    def stream_file(self, file_path: str, file_type: str, range_header: Optional[str] = None) -> Response:
        """Stream a media file with support for range requests"""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        file_size = os.path.getsize(file_path)
        
        # Handle range requests for video streaming
        if range_header and file_type.startswith('video/'):
            logger.info(f"Range request for video: {range_header}")
            
            try:
                # Parse range header
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
                    with open(file_path, 'rb') as f:
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
        return send_file(file_path, as_attachment=False, mimetype=file_type)
    
    def get_export_filename(self, export_type: str, format_type: str = 'csv') -> str:
        """Generate filename for export"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"pixelprobe_{export_type}_{timestamp}.{format_type}"