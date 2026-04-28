"""
时间线管理服务
"""

from datetime import datetime
from app.utils.logger import get_logger

logger = get_logger('mirofish.service.timeline_manager')


class TimelineManager:
    """时间线管理器"""
    
    def __init__(self):
        pass
    
    def create_timeline(self, world_id, events):
        """创建时间线"""
        try:
            # 按时间排序事件
            sorted_events = sorted(events, key=lambda x: x.get('date', ''))
            
            timeline = {
                "world_id": world_id,
                "events": sorted_events,
                "created_at": datetime.now().isoformat()
            }
            
            logger.info(f"为世界观 {world_id} 创建时间线，包含 {len(sorted_events)} 个事件")
            return timeline
            
        except Exception as e:
            logger.error(f"创建时间线失败: {str(e)}")
            return {
                "world_id": world_id,
                "events": [],
                "created_at": datetime.now().isoformat()
            }
    
    def get_timeline_range(self, timeline, start_date, end_date):
        """获取时间线指定范围的事件"""
        try:
            filtered_events = [
                event for event in timeline.get('events', [])
                if start_date <= event.get('date', '') <= end_date
            ]
            
            return {
                "world_id": timeline.get('world_id'),
                "events": filtered_events,
                "start_date": start_date,
                "end_date": end_date
            }
            
        except Exception as e:
            logger.error(f"获取时间线范围失败: {str(e)}")
            return {
                "world_id": timeline.get('world_id'),
                "events": [],
                "start_date": start_date,
                "end_date": end_date
            }
    
    def add_event_to_timeline(self, timeline, event):
        """向时间线添加事件"""
        try:
            events = timeline.get('events', [])
            events.append(event)
            # 重新排序
            sorted_events = sorted(events, key=lambda x: x.get('date', ''))
            
            timeline['events'] = sorted_events
            timeline['updated_at'] = datetime.now().isoformat()
            
            logger.info(f"向时间线添加事件: {event.get('name')}")
            return timeline
            
        except Exception as e:
            logger.error(f"添加事件失败: {str(e)}")
            return timeline
