import json
import os
import logging
import tempfile
import shutil
import time
import re
import hashlib
from pathlib import Path
from collections import OrderedDict
from typing import Dict, Any, Optional, List
from datetime import datetime
from config import Config

logger = logging.getLogger('StateManager')

class StateManager:
    """Улучшенный менеджер состояния с обработкой блокировок и резервным копированием"""
    
    VERSION = 1.4
    DEFAULT_MAX_ENTRIES = 1000
    BACKUP_DIR = "state_backups"
    LOCK_TIMEOUT = 60  # 60 секунд таймаут блокировки
    
    def __init__(self, state_file: str = 'bot_state.json', max_entries: int = DEFAULT_MAX_ENTRIES, config: Config = None):
        self.state_file = Path(state_file)
        self.max_entries = max_entries
        self.backup_dir = Path(self.BACKUP_DIR)
        self._lock_file = self.state_file.with_suffix('.lock')
        self.config = config
        self.lock_acquired = False
        
        # Очистка устаревших блокировок при инициализации
        self._cleanup_stale_lock()
        
        # Инициализация состояния
        self.state: Dict[str, Any] = {
            'sent_entries': OrderedDict(),
            'sent_hashes': OrderedDict(),
            'stats': {},
            'metadata': {
                'version': self.VERSION,
                'created_at': self._current_timestamp(),
                'last_modified': None,
                'initialized': True
            }
        }
        
        self._ensure_directories()
        self.load_state()

    def __enter__(self):
        """Поддержка контекстного менеджера для автоматического сохранения"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Автоматическое сохранение при выходе из контекста"""
        if exc_type is None:
            self.save_state()
        return False

    def _cleanup_stale_lock(self):
        """Удаляет устаревшие lock-файлы"""
        if self._lock_file.exists():
            try:
                lock_age = time.time() - self._lock_file.stat().st_mtime
                if lock_age > self.LOCK_TIMEOUT:
                    self._lock_file.unlink()
                    logger.warning(f"Removed stale lock file: {self._lock_file}")
            except Exception as e:
                logger.error(f"Failed to remove stale lock: {str(e)}")

    def _acquire_lock(self) -> bool:
        """Пытается получить файловую блокировку с таймаутом"""
        start_time = time.time()
        while time.time() - start_time < self.LOCK_TIMEOUT:
            try:
                if not self._lock_file.exists():
                    self._lock_file.touch()
                    self.lock_acquired = True
                    return True
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"Lock acquisition failed: {str(e)}")
        return False

    def _release_lock(self) -> None:
        """Освобождает файловую блокировку"""
        try:
            if self.lock_acquired and self._lock_file.exists():
                self._lock_file.unlink()
                self.lock_acquired = False
        except Exception as e:
            logger.error(f"Failed to release lock: {str(e)}")

    def _ensure_directories(self) -> None:
        """Создает необходимые директории"""
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            if self.state_file.parent:
                self.state_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"Directory creation failed: {e}")
            raise

    def _create_backup(self) -> Optional[Path]:
        """Создает резервную копию текущего состояния"""
        if not self.state_file.exists():
            return None
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = self.backup_dir / f"state_backup_{timestamp}.json"
        
        try:
            shutil.copy2(self.state_file, backup_file)
            logger.info(f"Created state backup: {backup_file}")
            return backup_file
        except Exception as e:
            logger.error(f"Backup creation failed: {e}")
            return None

    def _validate_state(self, state: Dict[str, Any]) -> bool:
        """Расширенная проверка целостности структуры состояния"""
        try:
            required_keys = {'sent_entries', 'sent_hashes', 'stats', 'metadata'}
            if not required_keys.issubset(state.keys()):
                missing = required_keys - state.keys()
                logger.warning(f"Missing required keys in state: {missing}")
                return False
                
            type_checks = [
                ('sent_entries', dict),
                ('sent_hashes', dict),
                ('stats', dict),
                ('metadata', dict)
            ]
            
            for key, expected_type in type_checks:
                if not isinstance(state[key], expected_type):
                    logger.warning(f"{key} should be a {expected_type.__name__}")
                    return False
                    
            for key in state['sent_entries']:
                if not self._is_valid_entry_id(key):
                    logger.warning(f"Invalid entry key format: {key}")
                    return False
                    
            for key in state['sent_hashes']:
                if not self._is_valid_hash(key):
                    logger.warning(f"Invalid hash key format: {key}")
                    return False
                    
            metadata = state['metadata']
            if not isinstance(metadata.get('version'), (int, float)):
                logger.warning("Invalid version in metadata")
                return False
                
            return True
        except Exception as e:
            logger.error(f"Validation error: {str(e)}", exc_info=True)
            return False

    @staticmethod
    def _is_valid_entry_id(entry_id: str) -> bool:
        """Проверяет валидность ID записи"""
        return isinstance(entry_id, str) and bool(entry_id)

    @staticmethod
    def _is_valid_hash(hash_value: str) -> bool:
        """Проверяет валидность хеш-значения"""
        return isinstance(hash_value, str) and len(hash_value) == 64 and bool(re.match(r'^[a-f0-9]{64}$', hash_value))

    def load_state(self) -> None:
        """Безопасная загрузка состояния с улучшенной обработкой ошибок"""
        if not self.state_file.exists():
            logger.info("No state file found, starting with fresh state")
            return
            
        try:
            with self.state_file.open('r', encoding='utf-8') as f:
                data = f.read()
                
            if not data.strip():
                raise ValueError("State file is empty")
                
            loaded_state = json.loads(data, object_pairs_hook=OrderedDict)
            loaded_state = self._migrate_state(loaded_state)
            
            if not self._validate_state(loaded_state):
                raise ValueError("Invalid state structure")
                
            self.state = loaded_state
            logger.info(f"State loaded successfully from {self.state_file}")
            
        except Exception as e:
            backup_file = self._create_backup()
            logger.error(f"Failed to load state: {str(e)}. Backup created: {backup_file}")
            
            self.state = {
                'sent_entries': OrderedDict(),
                'sent_hashes': OrderedDict(),
                'stats': {},
                'metadata': {
                    'version': self.VERSION,
                    'created_at': self._current_timestamp(),
                    'last_modified': None,
                    'recovery_reason': f"Original state corrupted: {str(e)}",
                    'backup_file': str(backup_file) if backup_file else None
                }
            }

    def _migrate_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Выполняет миграцию состояния из старых версий"""
        version = state.get('metadata', {}).get('version', 1.0)
        
        if version < 1.1:
            logger.info(f"Migrating state from version {version} to 1.1")
            if 'sent_entries' in state and isinstance(state['sent_entries'], list):
                state = self._convert_legacy_state(state)
                
        if version < self.VERSION:
            logger.info(f"Migrating state from version {version} to {self.VERSION}")
            state['metadata']['version'] = self.VERSION
            state['metadata'].setdefault('initialized', True)
            
        return state

    def save_state(self) -> bool:
        """Безопасное сохранение состояния с использованием временного файла и блокировки"""
        if not self._acquire_lock():
            logger.warning("Could not acquire lock for saving state")
            return False
            
        try:
            metadata_update = {
                'last_modified': self._current_timestamp(),
                'entries_count': len(self.state['sent_entries']),
                'hashes_count': len(self.state['sent_hashes'])
            }
            
            if self.config:
                metadata_update['enable_yagpt'] = self.config.ENABLE_YAGPT
                
            self.state['metadata'].update(metadata_update)
            
            # Создаем временный файл в той же директории, что и основной файл состояния
            temp_file = tempfile.NamedTemporaryFile(
                mode='w',
                encoding='utf-8',
                dir=str(self.state_file.parent),
                suffix='.tmp',
                delete=False
            )
            temp_path = Path(temp_file.name)
            try:
                # Записываем состояние во временный файл
                json.dump(self.state, temp_file, indent=2, ensure_ascii=False)
                temp_file.close()  # Закрываем файл, чтобы убедиться, что все записано
            except Exception as e:
                logger.error(f"Failed to write to temp file: {str(e)}")
                os.unlink(temp_path)
                return False
                
            # Валидация записанных данных
            try:
                with temp_path.open('r') as f:
                    json.load(f)
            except json.JSONDecodeError as ve:
                logger.error(f"Invalid JSON in temp state: {str(ve)}")
                os.unlink(temp_path)
                return False
            except Exception as e:
                logger.error(f"Error validating temp state: {str(e)}")
                os.unlink(temp_path)
                return False
                
            # Заменяем основной файл
            try:
                os.replace(temp_path, self.state_file)
            except Exception as e:
                logger.error(f"Failed to replace state file: {str(e)}")
                os.unlink(temp_path)
                return False
                
            logger.info(f"State saved successfully to {self.state_file}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving state: {str(e)}", exc_info=True)
            self._create_backup()
            return False
        finally:
            self._release_lock()

    @staticmethod
    def _current_timestamp() -> str:
        """Возвращает текущую временную метку в ISO формате"""
        return datetime.now().isoformat()

    def is_entry_sent(self, entry_id: str) -> bool:
        """Проверяет, был ли отправлен пост с данным ID"""
        return entry_id in self.state.get('sent_entries', {})

    def add_sent_entry(self, post: Dict) -> None:
        """Добавляет отправленный пост в историю"""
        post_id = post.get('post_id')
        if not post_id:
            logger.warning("Cannot add entry without post_id")
            return
            
        timestamp = self._current_timestamp()
        self.state['sent_entries'][post_id] = timestamp
        
        content_hash = self._generate_content_hash(post)
        if content_hash:
            self.state['sent_hashes'][content_hash] = timestamp
            
        if len(self.state['sent_entries']) > self.max_entries * 1.2:
            self.cleanup_old_entries()

    def _generate_content_hash(self, post: Dict) -> Optional[str]:
        """Генерирует хеш для контента поста"""
        try:
            content = f"{post.get('title', '')}{post.get('description', '')}"
            return hashlib.sha256(content.encode()).hexdigest()
        except Exception as e:
            logger.error(f"Failed to generate content hash: {e}")
            return None

    def is_hash_sent(self, hash_value: str) -> bool:
        """Проверяет, был ли хеш контента уже обработан"""
        return self._is_valid_hash(hash_value) and hash_value in self.state.get('sent_hashes', {})

    def cleanup_old_entries(self) -> int:
        """Очищает старые записи, возвращает количество удаленных"""
        current_count = len(self.state['sent_entries'])
        if current_count <= self.max_entries:
            return 0
            
        to_remove = current_count - self.max_entries
        removed_entries = 0
        removed_hashes = 0
        
        while len(self.state['sent_entries']) > self.max_entries:
            self.state['sent_entries'].popitem(last=False)
            removed_entries += 1
            
        if len(self.state['sent_hashes']) > self.max_entries * 1.5:
            oldest_entry_time = next(iter(self.state['sent_entries'].values())), None
            if oldest_entry_time:
                hashes_to_remove = [
                    h for h, t in self.state['sent_hashes'].items()
                    if t < oldest_entry_time
                ]
                for h in hashes_to_remove[:to_remove]:
                    self.state['sent_hashes'].pop(h, None)
                    removed_hashes += 1
                    
        logger.info(f"Cleaned up {removed_entries} entries and {removed_hashes} hashes")
        return removed_entries + removed_hashes

    def compress_state(self) -> None:
        """Сжимает состояние, удаляя дубликаты и оптимизируя структуру"""
        hashes_to_keep = {
            self._generate_content_hash({'title': '', 'description': k})
            for k in self.state['sent_entries']
        }
        
        self.state['sent_hashes'] = OrderedDict(
            (k, v) for k, v in self.state['sent_hashes'].items()
            if k in hashes_to_keep
        )
        
        if 'stats' in self.state and not self.state['stats']:
            del self.state['stats']

    def get_stats(self) -> Dict[str, Any]:
        """Возвращает расширенную статистику состояния"""
        entries = self.state.get('sent_entries', {})
        hashes = self.state.get('sent_hashes', {})
        metadata = self.state.get('metadata', {})
        
        return {
            'entries_count': len(entries),
            'hashes_count': len(hashes),
            'oldest_entry': next(iter(entries.values()), None) if entries else None,
            'newest_entry': next(reversed(entries.values()), None) if entries else None,
            'version': metadata.get('version', 'unknown'),
            'last_modified': metadata.get('last_modified', 'never'),
            'initialized': metadata.get('initialized', False),
            'state_file': str(self.state_file.absolute()),
            'backups_count': len(list(self.backup_dir.glob('*.json'))) if self.backup_dir.exists() else 0
        }

    def update_stats(self, stats: Dict[str, Any]) -> None:
        """Обновляет статистику в состоянии"""
        if not isinstance(stats, dict):
            logger.warning("Stats should be a dictionary")
            return
            
        if 'stats' not in self.state:
            self.state['stats'] = {}
        
        self.state['stats'].update(stats)
        logger.debug(f"Updated stats with {len(stats)} items")

    def _convert_legacy_state(self, legacy_state: Dict[str, Any]) -> Dict[str, Any]:
        """Конвертирует старый формат состояния в новый"""
        new_state = {
            'sent_entries': OrderedDict(),
            'sent_hashes': OrderedDict(),
            'stats': legacy_state.get('stats', {}),
            'metadata': {
                'version': self.VERSION,
                'created_at': self._current_timestamp(),
                'last_modified': None,
                'converted_from_legacy': True
            }
        }
        
        for entry in legacy_state.get('sent_entries', []):
            if isinstance(entry, dict) and 'post_id' in entry:
                post_id = entry['post_id']
                pub_date = entry.get('pub_date', self._current_timestamp())
                new_state['sent_entries'][post_id] = pub_date
        
        if 'entry_hashes' in legacy_state and isinstance(legacy_state['entry_hashes'], list):
            for hash_val in legacy_state['entry_hashes']:
                if self._is_valid_hash(hash_val):
                    new_state['sent_hashes'][hash_val] = self._current_timestamp()
        
        return new_state

    def list_backups(self) -> List[Path]:
        """Возвращает список доступных резервных копий"""
        if not self.backup_dir.exists():
            return []
        return sorted(self.backup_dir.glob('state_backup_*.json'), reverse=True)

    def restore_from_backup(self, backup_path: Path) -> bool:
        """Восстанавливает состояние из резервной копии"""
        try:
            with backup_path.open('r', encoding='utf-8') as f:
                data = f.read()
                
            if not data.strip():
                raise ValueError("Backup file is empty")
                
            loaded_state = json.loads(data, object_pairs_hook=OrderedDict)
            
            if not self._validate_state(loaded_state):
                raise ValueError("Invalid backup state structure")
                
            self.state = loaded_state
            self.save_state()
            logger.info(f"Successfully restored state from backup: {backup_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to restore from backup: {e}")
            return False