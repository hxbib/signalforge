import io
from unittest import TestCase
from unittest.mock import Mock
from PIL import Image
from deal_models import Visual
from visual_recovery import second_look

class SecondLook(TestCase):
    def setUp(self):
        buf=io.BytesIO()
        Image.new("RGB",(400,400)).save(buf,format="JPEG")
        self.image=buf.getvalue()
        self.v=Visual(name="camera",barcode="",brand="Example",part_number="A1",variant_details=[],
                    query="camera",target_count="one",focus_box=[100,100,700,700],focus_words="",evidence=[])
        self.ok={"choice":"single","pass":True}
        self.low={"choice":"other","pass":False}

    def test_clear_photo_needs_no_second_call(self):
        inspector=Mock()
        _,_,_,receipt=second_look(self.image,"",self.v,self.ok,inspector)
        self.assertIsNone(receipt)
        inspector.assert_not_called()

    def test_obscured_single_recovers_without_buyer(self):
        inspector=Mock(return_value=(self.v,self.ok,{"model":"neon-test"}))
        cropped,_,route,receipt=second_look(self.image,"",self.v,self.low,inspector)
        self.assertNotEqual(cropped,self.image)
        self.assertTrue(route["pass"])
        self.assertEqual(receipt["event"],"auto_crop")
        inspector.assert_called_once()

    def test_two_items_and_generic_reference_never_autoselects(self):
        v=self.v.model_copy(update={"target_count":"many","focus_words":"this one"})
        inspector=Mock()
        _,_,route,_=second_look(self.image,"this one",v,{"choice":"multiple","pass":False},inspector)
        inspector.assert_not_called()
        self.assertFalse(route["pass"])

    def test_disagreement_does_not_clear_gate(self):
        other=self.v.model_copy(update={"part_number":"B4"})
        inspector=Mock(return_value=(other,self.ok,{"model":"neon-test"}))
        image,_,route,receipt=second_look(self.image,"",self.v,self.low,inspector)
        self.assertFalse(route["pass"])
        self.assertEqual(image,self.image)
        self.assertIsNotNone(receipt)

