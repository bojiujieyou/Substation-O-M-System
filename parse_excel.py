# parse_excel.py — Excel解析模块
import os
from openpyxl import load_workbook

class ExcelParseError(Exception):
    """Excel解析异常"""
    pass

def parse_station_excel(filepath):
    """
    解析变电站Excel文件

    返回结构:
    {
        'station': {'name': str, 'voltage_level': str, 'county': str, ...},
        'cameras': [{'camera_index': str, 'area': str, 'location': str, 'ip': str, 'port': int, 'channel': int}, ...]
    }

    Excel结构（根据数据源清单）:
    - 第1行：变电站名称
    - 第2-5行：站内设备
    - 第6-8行：摄像头区域
    - 第9-12行：通道端口
    - 第13行后：通道详情
    """
    if not os.path.exists(filepath):
        raise ExcelParseError(f"文件不存在: {filepath}")

    try:
        wb = load_workbook(filepath, read_only=True, data_only=True)
    except Exception as e:
        raise ExcelParseError(f"无法打开Excel文件: {filepath}, 错误: {e}")

    try:
        ws = wb.active
    except Exception as e:
        raise ExcelParseError(f"无法读取工作表: {filepath}, 错误: {e}")

    try:
        # 获取所有行
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            raise ExcelParseError(f"Excel文件为空: {filepath}")

        # 解析变电站信息（第1行）
        station_name = rows[0][0] if rows[0] and rows[0][0] else None
        if not station_name:
            raise ExcelParseError(f"变电站名称为空: {filepath}")

        # 解析电压等级（从文件名或内容推断，这里先留空让import时填充）
        voltage_level = extract_voltage_from_content(rows)

        # 解析县区（从文件路径推断）
        county = extract_county_from_path(filepath)

        # 解析摄像头信息
        cameras = parse_camera_rows(rows)

        return {
            'station': {
                'name': str(station_name).strip(),
                'voltage_level': voltage_level,
                'county': county,
                'location': '',
                'ip_range': '',
                'nvr_ip': '',
                'nvr_port': None,
            },
            'cameras': cameras
        }

    except ExcelParseError:
        raise
    except Exception as e:
        raise ExcelParseError(f"解析Excel失败: {filepath}, 错误: {e}")
    finally:
        wb.close()

def extract_voltage_from_content(rows):
    """从内容中提取电压等级"""
    # 遍历前几行查找电压等级信息
    for row in rows[:10]:
        for cell in row:
            if cell:
                cell_str = str(cell).strip()
                # 匹配电压等级格式：220kV, 110kV, 35kV等
                if 'kV' in cell_str or 'kv' in cell_str.lower():
                    # 提取电压值
                    import re
                    match = re.search(r'(\d+)\s*[kK][vV]', cell_str)
                    if match:
                        return match.group(1) + 'kV'
    return ''

def extract_county_from_path(filepath):
    """从文件路径提取县区名称"""
    # 路径格式: .../图像监控设备资料/县名/变电站Excel
    parts = filepath.split(os.sep)
    for i, part in enumerate(parts):
        if part == '图像监控设备资料' and i + 1 < len(parts):
            return parts[i + 1]
    return ''

def parse_camera_rows(rows):
    """
    解析摄像头区域和通道信息

    Excel有两种格式：
    格式A (丽水变.xlsx): 表头在Row 17，IP在Col 3
    格式B (四都变.xlsx): 表头在Row 16，IP在Col 4

    动态查找IP列位置：
    - 找到包含'通道'和'IP地址'的行作为表头
    - 确定IP列的索引位置
    """
    cameras = []

    # 查找表头行（包含'通道'和'IP地址'的行）
    header_row_idx = None
    ip_col_idx = None
    for idx, row in enumerate(rows):
        if not row:
            continue
        # 查找包含'通道'的列
        for col_idx, cell in enumerate(row):
            if cell and '通道' in str(cell).strip():
                # 找到通道列后，在同行的其他列中找IP地址
                for col_idx2, cell2 in enumerate(row):
                    cell2_str = str(cell2).strip().upper() if cell2 else ''
                    if cell2 and ('IP' in cell2_str):
                        header_row_idx = idx
                        ip_col_idx = col_idx2
                        break
                if header_row_idx is not None:
                    break
        if header_row_idx is not None:
            break

    if header_row_idx is None or ip_col_idx is None:
        return cameras

    # 从表头下一行开始解析摄像头数据
    for row_idx in range(header_row_idx + 1, len(rows)):
        row = rows[row_idx]
        if not row:
            continue

        camera_info = extract_camera_from_row(row, row_idx, ip_col_idx)
        if camera_info:
            cameras.append(camera_info)

    return cameras

def extract_camera_from_row(row, row_idx, ip_col_idx=3):
    """从单行数据中提取摄像头信息

    Args:
        row: 行数据
        row_idx: 行索引
        ip_col_idx: IP列的索引（动态确定）
    """
    if not row or len(row) <= ip_col_idx:
        return None

    # 检查通道号
    channel_cell = row[0]
    if not channel_cell:
        return None
    channel_str = str(channel_cell).strip()
    if '通道' not in channel_str:
        return None

    # 提取通道号数字
    import re
    match = re.search(r'通道(\d+)', channel_str)
    camera_index = match.group(1) if match else ''

    # IP在动态确定的列
    ip_cell = row[ip_col_idx]
    if not ip_cell or not isinstance(ip_cell, str):
        return None

    ip = ip_cell.strip()
    if not re.match(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', ip):
        return None

    # 位置描述在第2列（索引1）
    location = str(row[1]).strip() if len(row) > 1 and row[1] else ''

    return {
        'camera_index': camera_index,
        'area': '',
        'location': location,
        'ip_address': ip,
        'channel_port': None,
        'channel_number': int(camera_index) if camera_index.isdigit() else None,
    }

def validate_excel_structure(filepath):
    """
    预检Excel结构
    返回: {'valid': bool, 'rows': int, 'errors': list}
    """
    result = {'valid': True, 'rows': 0, 'errors': []}

    try:
        wb = load_workbook(filepath, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        result['rows'] = len(rows)
        wb.close()

        # 基本验证
        if result['rows'] < 10:
            result['errors'].append(f"数据行数过少: {result['rows']}")

        if not rows or not rows[0][0]:
            result['valid'] = False
            result['errors'].append("变电站名称为空")

    except Exception as e:
        result['valid'] = False
        result['errors'].append(str(e))

    return result

if __name__ == '__main__':
    # 测试
    import sys
    if len(sys.argv) > 1:
        result = validate_excel_structure(sys.argv[1])
        print(f"预检结果: {result}")
