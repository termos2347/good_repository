import os
import re
import time
import asyncio
import hashlib
import logging
from typing import Tuple, Optional, List, Union
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from functools import partial
from PIL import Image, ImageDraw, ImageFont, ImageOps
from PIL.ImageFont import FreeTypeFont, ImageFont as BaseImageFont

FontType = Union[FreeTypeFont, BaseImageFont]

logger = logging.getLogger('AsyncImageGenerator')

class AsyncImageGenerator:
    def __init__(self, config):
        self.config = config
        self.templates_dir = config.TEMPLATES_DIR
        self.fonts_dir = config.FONTS_DIR
        self.output_dir = config.OUTPUT_DIR
        # Используем ThreadPoolExecutor вместо ProcessPoolExecutor
        self.executor = ThreadPoolExecutor(max_workers=config.IMAGE_GENERATION_WORKERS)
        
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.templates_dir, exist_ok=True)
        os.makedirs(self.fonts_dir, exist_ok=True)

    async def generate_image(self, title: str) -> Optional[str]:
        try:
            loop = asyncio.get_running_loop()
            # Выносим всю логику в отдельную синхронную функцию
            result = await loop.run_in_executor(
                self.executor,
                self._generate_image_sync,
                title
            )
            return result
        except Exception as e:
            logger.error(f"Image generation failed: {str(e)}")
            return None

    def _generate_image_sync(self, title: str) -> Optional[str]:
        """Синхронная версия генерации изображения"""
        try:
            start_time = time.time()
            logger.info(f"Generating image for: {title[:50]}...")
            
            # Поиск шаблонов
            templates = []
            if os.path.exists(self.templates_dir):
                templates = [f for f in os.listdir(self.templates_dir) 
                            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
            
            # Создание изображения
            if not templates:
                img = Image.new('RGB', (self.config.MAX_IMAGE_WIDTH, self.config.MAX_IMAGE_HEIGHT), (40, 40, 40))
            else:
                template_path = os.path.join(self.templates_dir, templates[0])
                img = Image.open(template_path).convert('RGB')
                img = ImageOps.fit(img, (self.config.MAX_IMAGE_WIDTH, self.config.MAX_IMAGE_HEIGHT))
            
            # Работа с шрифтом
            font_path = os.path.join(self.fonts_dir, self.config.DEFAULT_FONT)
            if not os.path.exists(font_path):
                font = ImageFont.load_default()
            else:
                font = ImageFont.truetype(font_path, size=48)
            
            # Отрисовка текста
            draw = ImageDraw.Draw(img)
            lines = self._wrap_text(title, draw, font, int(img.width * 0.8))
            
            # Вычисление позиции текста
            y_position = (img.height - sum(
                [draw.textbbox((0, 0), line, font=font)[3] - draw.textbbox((0, 0), line, font=font)[1] 
                    for line in lines]
            ) * 1.2) // 2
            
            for line in lines:
                bbox = draw.textbbox((0, 0), line, font=font)
                x_position = (img.width - (bbox[2] - bbox[0])) // 2
                draw.text(
                    (x_position, y_position),
                    line,
                    font=font,
                    fill=tuple(self.config.TEXT_COLOR),
                    stroke_fill=tuple(self.config.STROKE_COLOR),
                    stroke_width=self.config.STROKE_WIDTH
                )
                y_position += (bbox[3] - bbox[1]) * 1.2
            
            # Сохранение изображения
            filename = f"post_{int(time.time() * 1000)}.jpg"
            output_path = os.path.join(self.output_dir, filename)
            img.save(output_path, quality=85, optimize=True)
            
            logger.info(f"Image generated in {time.time() - start_time:.2f}s: {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"Image generation sync error: {str(e)}")
            return None

    def _sync_generate_image(self, title: str) -> Optional[str]:
        start_time = time.time()
        logger.info(f"Generating image for: {title[:50]}...")
        
        try:
            templates = [f for f in os.listdir(self.templates_dir) 
                        if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
            
            if not templates:
                logger.info("Using blank image (no templates available)")
                img = Image.new('RGB', (self.config.MAX_IMAGE_WIDTH, self.config.MAX_IMAGE_HEIGHT), (40, 40, 40))
            else:
                template_file = templates[0]
                template_path = os.path.join(self.templates_dir, template_file)
                logger.info(f"Using template: {template_file}")
                img = Image.open(template_path).convert('RGB')
                img = ImageOps.fit(img, (self.config.MAX_IMAGE_WIDTH, self.config.MAX_IMAGE_HEIGHT))
            
            font_path = os.path.join(self.fonts_dir, self.config.DEFAULT_FONT)
            if not os.path.exists(font_path):
                logger.warning(f"Font not found: {font_path}. Using default font.")
                font = ImageFont.load_default()
                font_size = 36
            else:
                font_size = 48
                font = ImageFont.truetype(font_path, font_size)
                logger.debug(f"Using font: {font_path} size {font_size}")
            
            draw = ImageDraw.Draw(img)
            lines = self._wrap_text(title, draw, font, int(img.width * 0.8))
            
            line_heights = []
            for line in lines:
                bbox_line = draw.textbbox((0, 0), line, font=font)
                line_heights.append(bbox_line[3] - bbox_line[1])
            
            total_height = sum(line_heights) * 1.2
            y_position = (img.height - total_height) // 2
            
            for i, line in enumerate(lines):
                bbox_line = draw.textbbox((0, 0), line, font=font)
                line_width = bbox_line[2] - bbox_line[0]
                x_position = (img.width - line_width) // 2
                
                draw.text(
                    (x_position, y_position),
                    line,
                    font=font,
                    fill=tuple(self.config.TEXT_COLOR),
                    stroke_fill=tuple(self.config.STROKE_COLOR),
                    stroke_width=self.config.STROKE_WIDTH
                )
                y_position += line_heights[i] * 1.2
            
            filename = f"post_{int(time.time() * 1000)}.jpg"
            output_path = os.path.join(self.output_dir, filename)
            img.save(output_path, quality=85, optimize=True)
            
            logger.info(f"Image generated in {time.time() - start_time:.2f}s: {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"Image generation failed: {str(e)}")
            return None

    def _wrap_text(self, text: str, draw: ImageDraw.ImageDraw, 
               font: FontType, max_width: int) -> list:
        text = self._sanitize_text(text)
        max_chars = self.config.MAX_TITLE_LENGTH
        if len(text) > max_chars:
            text = text[:max_chars-3] + "..."
        
        words = text.split()
        lines = []
        current_line = ""
        
        for word in words:
            test_line = f"{current_line} {word}".strip()
            
            bbox = draw.textbbox((0, 0), test_line, font=font)
            text_width = bbox[2] - bbox[0]
            
            if text_width <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
                
                if len(lines) >= self.config.MAX_TEXT_LINES:
                    break
        
        if current_line and len(lines) < self.config.MAX_TEXT_LINES:
            lines.append(current_line)
        
        if len(lines) > self.config.MAX_TEXT_LINES:
            lines = lines[:self.config.MAX_TEXT_LINES]
            lines[-1] = lines[-1][:50] + "..." if len(lines[-1]) > 53 else lines[-1]
        
        return lines

    @staticmethod
    def _sanitize_text(text: str) -> str:
        if not text:
            return ""
        
        replacements = {
            '&apos;': "'",
            '&quot;': '"',
            '&amp;': '&',
            '&lt;': '<',
            '&gt;': '>'
        }
        
        for html_entity, replacement in replacements.items():
            text = text.replace(html_entity, replacement)
        
        text = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', text)
        
        return text.strip()

    async def cleanup_old_images(self, max_age_hours: int = 24) -> Tuple[int, float]:
        deleted = 0
        freed_mb = 0.0
        now = time.time()
        
        for filename in os.listdir(self.output_dir):
            filepath = os.path.join(self.output_dir, filename)
            if os.path.isfile(filepath):
                file_age_hours = (now - os.path.getmtime(filepath)) / 3600
                if file_age_hours > max_age_hours:
                    try:
                        freed_mb += os.path.getsize(filepath) / (1024 ** 2)
                        os.unlink(filepath)
                        deleted += 1
                    except Exception as e:
                        logging.error(f"Failed to delete {filepath}: {str(e)}")
        
        return deleted, freed_mb
    
    def shutdown(self):
        self.executor.shutdown(wait=True)

    def restart_executor(self):
        """Пересоздает executor после shutdown"""
        if hasattr(self, 'executor'):
            self.executor.shutdown(wait=True)
        self.executor = ThreadPoolExecutor(max_workers=self.config.IMAGE_GENERATION_WORKERS)