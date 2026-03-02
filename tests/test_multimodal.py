"""
Tests for multimodal service
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from services.multimodal import MultimodalService
from config import Config


@pytest.fixture
def config():
    """Mock config"""
    cfg = MagicMock(spec=Config)
    cfg.OPENAI_API_KEY = "test-key"
    return cfg


@pytest.fixture
def service(config):
    """Multimodal service instance"""
    return MultimodalService(config)


@pytest.mark.asyncio
async def test_understand_image(service):
    """Test image understanding"""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="A cat on a sofa"))]
    mock_response.usage = MagicMock(total_tokens=150)
    
    with patch.object(service.client.chat.completions, 'create', new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_response
        
        result = await service.understand_image(
            image_data=b"fake_image_data",
            prompt="What's in this image?"
        )
        
        assert result["description"] == "A cat on a sofa"
        assert result["tokens_used"] == 150
        assert result["model"] == "gpt-4-vision-preview"
        mock_create.assert_called_once()


@pytest.mark.asyncio
async def test_generate_image(service):
    """Test image generation"""
    mock_response = MagicMock()
    mock_response.data = [
        MagicMock(
            url="https://example.com/image.png",
            revised_prompt="A detailed cyberpunk cityscape"
        )
    ]
    
    with patch.object(service.client.images, 'generate', new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = mock_response
        
        result = await service.generate_image(
            prompt="cyberpunk city",
            size="1024x1024"
        )
        
        assert result["url"] == "https://example.com/image.png"
        assert result["revised_prompt"] == "A detailed cyberpunk cityscape"
        assert result["model"] == "dall-e-3"
        mock_generate.assert_called_once()


@pytest.mark.asyncio
async def test_image_to_image(service):
    """Test image transformation pipeline"""
    # Mock understanding
    mock_understand = AsyncMock(return_value={
        "description": "A red car in a parking lot",
        "tokens_used": 100,
        "model": "gpt-4-vision-preview"
    })
    
    # Mock generation
    mock_generate = AsyncMock(return_value={
        "url": "https://example.com/new_image.png",
        "revised_prompt": "A futuristic red car in a neon-lit parking lot",
        "model": "dall-e-3"
    })
    
    with patch.object(service, 'understand_image', mock_understand), \
         patch.object(service, 'generate_image', mock_generate):
        
        result = await service.image_to_image(
            image_data=b"fake_image",
            transformation_prompt="make it futuristic"
        )
        
        assert result["original_description"] == "A red car in a parking lot"
        assert result["new_image_url"] == "https://example.com/new_image.png"
        mock_understand.assert_called_once()
        mock_generate.assert_called_once()


@pytest.mark.asyncio
async def test_understand_image_error_handling(service):
    """Test error handling in image understanding"""
    with patch.object(service.client.chat.completions, 'create', new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = Exception("API Error")
        
        with pytest.raises(Exception, match="API Error"):
            await service.understand_image(b"fake_image", "test prompt")
