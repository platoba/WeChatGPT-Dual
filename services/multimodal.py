"""
Multimodal service: Image understanding (GPT-4V) + Image generation (DALL-E 3)
"""
import base64
import logging
from io import BytesIO
from typing import Optional, Dict, Any
from openai import AsyncOpenAI
from config import Config

logger = logging.getLogger(__name__)


class MultimodalService:
    """Handle image understanding and generation"""
    
    def __init__(self, config: Config):
        self.config = config
        self.client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
        self.vision_model = "gpt-4-vision-preview"
        self.dalle_model = "dall-e-3"
    
    async def understand_image(
        self, 
        image_data: bytes, 
        prompt: str = "What's in this image?",
        detail: str = "auto"
    ) -> Dict[str, Any]:
        """
        Analyze image with GPT-4V
        
        Args:
            image_data: Raw image bytes
            prompt: Question about the image
            detail: "low" | "high" | "auto" (affects token cost)
        
        Returns:
            {
                "description": str,
                "tokens_used": int,
                "model": str
            }
        """
        try:
            # Encode image to base64
            b64_image = base64.b64encode(image_data).decode('utf-8')
            
            response = await self.client.chat.completions.create(
                model=self.vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{b64_image}",
                                    "detail": detail
                                }
                            }
                        ]
                    }
                ],
                max_tokens=500
            )
            
            description = response.choices[0].message.content
            tokens_used = response.usage.total_tokens
            
            logger.info(f"Image analyzed: {tokens_used} tokens")
            
            return {
                "description": description,
                "tokens_used": tokens_used,
                "model": self.vision_model
            }
            
        except Exception as e:
            logger.error(f"Image understanding failed: {e}")
            raise
    
    async def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "standard",
        style: str = "vivid"
    ) -> Dict[str, Any]:
        """
        Generate image with DALL-E 3
        
        Args:
            prompt: Image description
            size: "1024x1024" | "1792x1024" | "1024x1792"
            quality: "standard" | "hd"
            style: "vivid" | "natural"
        
        Returns:
            {
                "url": str,
                "revised_prompt": str,
                "model": str
            }
        """
        try:
            response = await self.client.images.generate(
                model=self.dalle_model,
                prompt=prompt,
                size=size,
                quality=quality,
                style=style,
                n=1
            )
            
            image_url = response.data[0].url
            revised_prompt = response.data[0].revised_prompt
            
            logger.info(f"Image generated: {image_url}")
            
            return {
                "url": image_url,
                "revised_prompt": revised_prompt,
                "model": self.dalle_model
            }
            
        except Exception as e:
            logger.error(f"Image generation failed: {e}")
            raise
    
    async def image_to_image(
        self,
        image_data: bytes,
        transformation_prompt: str
    ) -> Dict[str, Any]:
        """
        Understand image + generate new one based on description
        
        Args:
            image_data: Original image bytes
            transformation_prompt: How to transform (e.g., "make it cyberpunk style")
        
        Returns:
            {
                "original_description": str,
                "new_image_url": str,
                "revised_prompt": str
            }
        """
        # Step 1: Understand original image
        understanding = await self.understand_image(
            image_data,
            prompt=f"Describe this image in detail. {transformation_prompt}"
        )
        
        # Step 2: Generate new image based on description
        generation = await self.generate_image(
            prompt=understanding["description"]
        )
        
        return {
            "original_description": understanding["description"],
            "new_image_url": generation["url"],
            "revised_prompt": generation["revised_prompt"]
        }
