"""
Telegram handlers for multimodal features
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
from services.multimodal import MultimodalService

logger = logging.getLogger(__name__)


class MultimodalHandler:
    """Handle image understanding and generation in Telegram"""
    
    def __init__(self, multimodal_service: MultimodalService):
        self.service = multimodal_service
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle photo messages - analyze with GPT-4V
        """
        try:
            # Get the largest photo
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            
            # Download image
            image_bytes = await file.download_as_bytearray()
            
            # Get caption as prompt (or use default)
            prompt = update.message.caption or "Describe this image in detail."
            
            # Analyze image
            await update.message.reply_text("🔍 Analyzing image...")
            result = await self.service.understand_image(
                image_data=bytes(image_bytes),
                prompt=prompt
            )
            
            # Reply with description
            response = f"📸 **Image Analysis**\n\n{result['description']}\n\n"
            response += f"_Tokens used: {result['tokens_used']}_"
            
            await update.message.reply_text(
                response,
                parse_mode="Markdown"
            )
            
        except Exception as e:
            logger.error(f"Photo handling error: {e}")
            await update.message.reply_text(
                f"❌ Failed to analyze image: {str(e)}"
            )
    
    async def handle_generate_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /generate <prompt> - Generate image with DALL-E 3
        """
        try:
            # Extract prompt
            prompt = " ".join(context.args) if context.args else None
            
            if not prompt:
                await update.message.reply_text(
                    "Usage: /generate <description>\n"
                    "Example: /generate a cyberpunk cat wearing sunglasses"
                )
                return
            
            # Generate image
            await update.message.reply_text("🎨 Generating image...")
            result = await self.service.generate_image(prompt=prompt)
            
            # Send image
            await update.message.reply_photo(
                photo=result["url"],
                caption=f"✨ Generated from: {result['revised_prompt']}"
            )
            
        except Exception as e:
            logger.error(f"Image generation error: {e}")
            await update.message.reply_text(
                f"❌ Failed to generate image: {str(e)}"
            )
    
    async def handle_transform_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Reply to a photo with /transform <style> - Transform image style
        """
        try:
            # Check if replying to a photo
            if not update.message.reply_to_message or not update.message.reply_to_message.photo:
                await update.message.reply_text(
                    "❌ Reply to a photo with /transform <style>\n"
                    "Example: /transform make it look like a watercolor painting"
                )
                return
            
            # Get transformation prompt
            transformation = " ".join(context.args) if context.args else "transform this image"
            
            # Get original photo
            photo = update.message.reply_to_message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            image_bytes = await file.download_as_bytearray()
            
            # Transform
            await update.message.reply_text("🔄 Transforming image...")
            result = await self.service.image_to_image(
                image_data=bytes(image_bytes),
                transformation_prompt=transformation
            )
            
            # Send new image
            caption = f"🎨 **Transformation**\n\n"
            caption += f"Original: {result['original_description'][:100]}...\n\n"
            caption += f"New: {result['revised_prompt']}"
            
            await update.message.reply_photo(
                photo=result["new_image_url"],
                caption=caption
            )
            
        except Exception as e:
            logger.error(f"Image transformation error: {e}")
            await update.message.reply_text(
                f"❌ Failed to transform image: {str(e)}"
            )


def register_handlers(application, multimodal_service: MultimodalService):
    """Register multimodal handlers with Telegram application"""
    handler = MultimodalHandler(multimodal_service)
    
    # Photo handler (auto-analyze)
    application.add_handler(
        MessageHandler(filters.PHOTO, handler.handle_photo)
    )
    
    # Commands
    from telegram.ext import CommandHandler
    application.add_handler(
        CommandHandler("generate", handler.handle_generate_command)
    )
    application.add_handler(
        CommandHandler("transform", handler.handle_transform_command)
    )
