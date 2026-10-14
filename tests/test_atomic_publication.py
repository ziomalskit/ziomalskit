"""F3/F4: atomic no-overwrite publication and real hard-exit recovery."""
import asyncio
import errno
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from tests.helpers import load_controller
from tests.test_artifact_storage import BODY, artifact
from h3.scripts import model_downloads as downloads, model_acceptance as acceptance

ROOT=Path(__file__).resolve().parents[1]


class UploadPublicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_dangling_symlink_upload_regression_cannot_escape_input(self):
        from fastapi import UploadFile
        with load_controller() as m:
            m.COMFY_INPUT_DIR.mkdir()
            outside=m.COMFY_INPUT_DIR.parent/'outside.bin'
            target=m.COMFY_INPUT_DIR/'reference.png';target.symlink_to(outside)
            result=await m.upload(UploadFile(filename='reference.png',file=io.BytesIO(BODY)))
            self.assertFalse(outside.exists())
            self.assertTrue(target.is_symlink())
            self.assertNotEqual(result['filename'],'reference.png')
            uploaded=m.COMFY_INPUT_DIR/result['filename']
            self.assertEqual(uploaded.read_bytes(),BODY)
            self.assertEqual(uploaded.stat().st_nlink,1)

    async def test_existing_file_and_live_link_preserved_while_complete_upload_published(self):
        from fastapi import UploadFile
        with load_controller() as m:
            m.COMFY_INPUT_DIR.mkdir()
            existing=m.COMFY_INPUT_DIR/'reference.png';existing.write_bytes(b'preserve')
            for name in ('reference.png','link.png'):
                if name=='link.png':(m.COMFY_INPUT_DIR/name).symlink_to(existing)
                result=await m.upload(UploadFile(filename=name,file=io.BytesIO(BODY)))
                self.assertNotEqual(result['filename'],name)
                self.assertEqual(existing.read_bytes(),b'preserve')
                self.assertEqual((m.COMFY_INPUT_DIR/result['filename']).read_bytes(),BODY)

    async def test_partial_read_failure_never_exposes_final_or_deletes_retained_file(self):
        from fastapi import UploadFile
        class Broken:
            def __init__(self):self.once=False
            def read(self,_):
                if self.once:raise OSError('synthetic interrupted upload')
                self.once=True;return b'partial'
        with load_controller() as m:
            m.COMFY_INPUT_DIR.mkdir()
            retained=m.COMFY_INPUT_DIR/'.unknown.partial';retained.write_bytes(b'preserve')
            with self.assertRaises(OSError):await m.upload(UploadFile(filename='new.png',file=Broken()))
            self.assertFalse((m.COMFY_INPUT_DIR/'new.png').exists())
            self.assertEqual(list(m.COMFY_INPUT_DIR.iterdir()),[retained])
            self.assertEqual(retained.read_bytes(),b'preserve')

    async def test_ancestor_symlink_is_refused_without_creating_outside_directory(self):
        from fastapi import UploadFile
        with load_controller() as m:
            outside=m.COMFY_INPUT_DIR.parent/'outside';outside.mkdir()
            link=m.COMFY_INPUT_DIR.parent/'link';link.symlink_to(outside,target_is_directory=True)
            m.COMFY_INPUT_DIR=link/'new-input'
            with self.assertRaises(OSError):await m.upload(UploadFile(filename='new.png',file=io.BytesIO(BODY)))
            self.assertEqual(list(outside.iterdir()),[])

    async def test_publication_collision_race_retries_without_overwriting_new_target(self):
        from fastapi import UploadFile
        with load_controller() as m:
            uploads=sys.modules[m.__package__+'.uploads'];original=uploads.publish_noreplace;calls=[]
            def race(directory,temporary,name):
                calls.append(name)
                if len(calls)==1:
                    fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,dir_fd=directory)
                    os.write(fd,b'other owner');os.close(fd)
                return original(directory,temporary,name)
            with patch.object(uploads,'publish_noreplace',side_effect=race):
                result=await m.upload(UploadFile(filename='new.png',file=io.BytesIO(BODY)))
            self.assertEqual((m.COMFY_INPUT_DIR/'new.png').read_bytes(),b'other owner')
            self.assertEqual((m.COMFY_INPUT_DIR/result['filename']).read_bytes(),BODY)
            self.assertEqual(len(calls),2)


class ModelPublicationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)/'acceptance'
        _,self.approval=acceptance.load_selection()
        acceptance.require_store(self.root,self.approval,create=True)
        self.models=self.root/'models'
        self.item=artifact()

    def download(self,item,stage):
        path=stage/item['repository_path']
        if not path.exists():path.write_bytes(BODY)
        return path

    def crash(self,boundary):
        code='''
import os,sys,stat
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from h3.scripts import model_downloads as download
from tests.test_artifact_storage import BODY,artifact
before=sys.argv[3]=='before'
if hasattr(download,'publish_noreplace'):
    original=download.publish_noreplace
    def publish(*args,**kwargs):
        if before:os._exit(77)
        original_fsync=os.fsync
        def sync(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):os._exit(77)
            return original_fsync(descriptor)
        os.fsync=sync
        original(*args,**kwargs)
        os._exit(77)
    download.publish_noreplace=publish
else:
    original=os.link
    def publish(*args,**kwargs):
        if before:os._exit(77)
        original(*args,**kwargs)
        os._exit(77)
    os.link=publish
def tiny(item,stage):
    path=stage/item['repository_path'];path.write_bytes(BODY);return path
download.ensure_download(Path(sys.argv[2])/'models',artifact(),downloader=tiny)
'''
        result=subprocess.run([sys.executable,'-B','-c',code,str(ROOT),str(self.root),boundary],
                              capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,77,result.stderr)

    def test_original_post_publication_hardkill_regression_has_one_link_and_safe_offline_retry(self):
        self.crash('after')
        final=self.models/self.item['destination']
        self.assertEqual(final.stat().st_nlink,1,'published inode must never have a retained second name')
        acceptance.check_model_path(self.root,self.item)
        plan=acceptance.plan(self.root,[self.item],self.approval)
        self.assertEqual(plan['missing_download_bytes'],0)
        downloader=Mock(side_effect=AssertionError('correct published bytes must not redownload'))
        result=downloads.ensure_download(self.models,self.item,downloader=downloader)
        self.assertTrue(result['reused']);downloader.assert_not_called()
        self.assertEqual(result['sha256'],self.item['sha256'])
        self.assertFalse(list(final.parent.glob('.model.gguf.*.partial')))

    def test_hardkill_before_publication_keeps_staging_and_unknown_partial_then_retries(self):
        self.crash('before')
        final=self.models/self.item['destination']
        self.assertFalse(final.exists())
        retained=list(final.parent.glob('.model.gguf.*.partial'))
        self.assertEqual(len(retained),1)
        original=retained[0].read_bytes()
        result=downloads.ensure_download(self.models,self.item,downloader=self.download)
        self.assertFalse(result['reused'])
        self.assertEqual(final.read_bytes(),BODY)
        self.assertEqual(retained[0].read_bytes(),original,'unknown retained partials must be preserved')
        self.assertEqual(final.stat().st_nlink,1)

    def test_corrupt_existing_file_never_downloaded_or_overwritten(self):
        final=self.models/self.item['destination'];final.parent.mkdir(parents=True)
        final.write_bytes(b'x'+BODY[1:]);downloader=Mock()
        with self.assertRaisesRegex(ValueError,'SHA256'):downloads.ensure_download(self.models,self.item,downloader=downloader)
        downloader.assert_not_called();self.assertEqual(final.read_bytes(),b'x'+BODY[1:])

    def test_corrupt_source_is_not_published_and_correct_retry_succeeds(self):
        def bad(item,stage):
            path=stage/item['repository_path'];path.write_bytes(b'x'+BODY[1:]);return path
        with self.assertRaisesRegex(ValueError,'SHA256'):downloads.ensure_download(self.models,self.item,downloader=bad)
        self.assertFalse((self.models/self.item['destination']).exists())
        def good(item,stage):
            path=stage/item['repository_path'];path.write_bytes(BODY);return path
        result=downloads.ensure_download(self.models,self.item,downloader=good)
        self.assertEqual(result['sha256'],self.item['sha256'])

    def test_destination_symlink_race_refused_without_overwrite_or_external_write(self):
        original=downloads.publish_noreplace
        outside=self.root.parent/'outside.gguf'
        def race(directory,temporary,name):
            os.symlink(outside,name,dir_fd=directory)
            return original(directory,temporary,name)
        with patch.object(downloads,'publish_noreplace',side_effect=race),self.assertRaises(FileExistsError):
            downloads.ensure_download(self.models,self.item,downloader=self.download)
        self.assertFalse(outside.exists())
        self.assertTrue((self.models/self.item['destination']).is_symlink())

    def test_unowned_temporary_name_collision_is_preserved(self):
        final=self.models/self.item['destination'];final.parent.mkdir(parents=True)
        retained=final.parent/'.model.gguf.fixture.partial';retained.write_bytes(b'preserve unverified data')
        with patch.object(downloads.uuid,'uuid4',return_value=types.SimpleNamespace(hex='fixture')), \
             self.assertRaises(FileExistsError):
            downloads.ensure_download(self.models,self.item,downloader=self.download)
        self.assertEqual(retained.read_bytes(),b'preserve unverified data')
        self.assertFalse(final.exists())

    def test_directory_fsync_failure_after_atomic_publish_recovers_without_download(self):
        original=downloads.publish_noreplace
        def published(*args):
            original(*args);raise OSError(errno.EIO,'synthetic post-publication fsync failure')
        with patch.object(downloads,'publish_noreplace',side_effect=published),self.assertRaises(OSError):
            downloads.ensure_download(self.models,self.item,downloader=self.download)
        acceptance.check_model_path(self.root,self.item)
        result=downloads.ensure_download(self.models,self.item,downloader=Mock(side_effect=AssertionError('no download')))
        self.assertTrue(result['reused'])

    def test_unsupported_atomic_rename_fails_closed_preserving_existing_data(self):
        from h3.app import file_publication
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'partial';source.write_bytes(BODY)
            descriptor=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
            try:
                with patch.object(file_publication.ctypes,'CDLL',return_value=object()),self.assertRaises(OSError) as error:
                    file_publication.publish_noreplace(descriptor,'partial','final')
                self.assertEqual(error.exception.errno,errno.ENOSYS)
                self.assertEqual(source.read_bytes(),BODY);self.assertFalse((root/'final').exists())
            finally:os.close(descriptor)
