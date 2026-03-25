# test_parse_excel.py — Excel解析模块测试
import os
import sys
import pytest
import tempfile

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parse_excel import (
    parse_station_excel,
    validate_excel_structure,
    extract_voltage_from_content,
    extract_county_from_path,
    parse_camera_rows,
    extract_camera_from_row,
    ExcelParseError
)

class TestExtractVoltageFromContent:
    """测试电压等级提取"""

    def test_220kv(self):
        rows = [[None, '220kV变电站'], [None], [None]]
        assert extract_voltage_from_content(rows) == '220kV'

    def test_110kv(self):
        rows = [[None, None, '电压:110kV'], [None]]
        assert extract_voltage_from_content(rows) == '110kV'

    def test_no_voltage(self):
        rows = [[None, '测试'], [None]]
        assert extract_voltage_from_content(rows) == ''

    def test_empty_rows(self):
        assert extract_voltage_from_content([]) == ''
        assert extract_voltage_from_content([[None, None]]) == ''


class TestExtractCountyFromPath:
    """测试县区提取"""

    def test_lishui(self):
        path = r'e:\办公\图像监控\图像监控设备资料\丽水\某变电站.xlsx'
        assert extract_county_from_path(path) == '丽水'

    def test_longyan(self):
        path = r'e:\办公\图像监控\图像监控设备资料\龙泉\某变电站.xlsx'
        assert extract_county_from_path(path) == '龙泉'

    def test_no_county(self):
        path = r'e:\其他路径\某文件.xlsx'
        assert extract_county_from_path(path) == ''


class TestExtractCameraFromRow:
    """测试摄像头信息提取"""

    def test_valid_camera_row(self):
        """正确格式: 通道1, 位置描述, IP"""
        row = ['通道1', '220kV场地北侧-1#球', None, '192.168.1.100', None, '2018年']
        result = extract_camera_from_row(row, 20)
        assert result is not None
        assert result['ip_address'] == '192.168.1.100'
        assert result['camera_index'] == '1'
        assert result['location'] == '220kV场地北侧-1#球'

    def test_channel_10(self):
        """通道10的解析"""
        row = ['通道10', '35kV开关室1-10#球', None, '192.168.1.10', None, '2018年']
        result = extract_camera_from_row(row, 20)
        assert result is not None
        assert result['camera_index'] == '10'
        assert result['channel_number'] == 10

    def test_no_channel_header(self):
        """没有'通道'关键字的行应返回None"""
        row = ['设备', '某设备', None, '192.168.1.100', None, None]
        result = extract_camera_from_row(row, 20)
        assert result is None

    def test_invalid_ip(self):
        """IP格式错误"""
        row = ['通道1', '位置描述', None, 'invalid-ip', None, None]
        result = extract_camera_from_row(row, 20)
        assert result is None

    def test_empty_row(self):
        row = [None, None, None, None, None]
        result = extract_camera_from_row(row, 20)
        assert result is None

    def test_no_ip_column(self):
        """没有IP列"""
        row = ['通道1', '位置描述', '一些数据']
        result = extract_camera_from_row(row, 20)
        assert result is None


class TestValidateExcelStructure:
    """测试Excel结构验证"""

    def test_file_not_found(self):
        result = validate_excel_structure(r'C:\nonexistent\file.xlsx')
        assert result['valid'] == False
        assert len(result['errors']) > 0


class TestExcelParseError:
    """测试异常类型"""

    def test_is_exception(self):
        assert issubclass(ExcelParseError, Exception)

    def test_message(self):
        error = ExcelParseError('测试错误')
        assert str(error) == '测试错误'
