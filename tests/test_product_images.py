import base64
import io
from unittest.mock import patch
from unittest.mock import Mock
from urllib.parse import urlsplit
import pytest
from PIL import Image
from monitor import product_images as images


def test_image_normalization_reuses_pillow():
    source=io.BytesIO();Image.new('RGBA',(1600,800),(20,100,60,255)).save(source,format='PNG')
    result=images.normalize(source.getvalue())
    assert result['mime']=='image/jpeg'
    with Image.open(io.BytesIO(base64.b64decode(result['data']))) as image:
        assert image.size==(1200,600) and image.format=='JPEG'


@pytest.mark.parametrize('url',['file:///etc/passwd','http://user:pass@example.com/a.jpg','http://example.com:8080/a.jpg','http://127.0.0.1/a.jpg','http://[::1]/a.jpg'])
def test_unsafe_image_targets_rejected(url):
    with pytest.raises(ValueError):images.target(url)


def test_mixed_dns_private_and_public_rejected():
    answers=[(2,1,6,'',('93.184.216.34',443)),(2,1,6,'',('127.0.0.1',443))]
    with patch.object(images.socket,'getaddrinfo',return_value=answers),pytest.raises(ValueError):
        images.target('https://shop.example/a.jpg')


def test_public_address_is_selected_for_pinned_connection():
    with patch.object(images.socket,'getaddrinfo',return_value=[(2,1,6,'',('93.184.216.34',443))]):
        _,host,port,ip=images.target('https://shop.example/a.jpg')
    assert (host,port,ip)==('shop.example',443,'93.184.216.34')


def test_not_an_image_rejected():
    with pytest.raises(Exception):images.normalize(b'<html>403 Forbidden</html>')


def test_denied_image_is_not_retried_or_redirected():
    connection=Mock();connection.getresponse.return_value.status=403
    with patch.object(images,'target',return_value=(urlsplit('http://shop.example/a.jpg'),'shop.example',80,'93.184.216.34')),patch.object(images.http.client,'HTTPConnection',return_value=connection),patch.object(images.socket,'create_connection') as connect:
        with pytest.raises(ValueError):images.download('http://shop.example/a.jpg')
        assert connection.request.call_count==1
        connect.assert_called_once_with(('93.184.216.34',80),timeout=8)
        connection.close.assert_called_once()
