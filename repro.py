"""POSIX syscall model, NOT a pnpm execution or native regression receipt."""
import json, multiprocessing, os, pathlib, shutil, tempfile

PAYLOAD = b'{"name":"native-mutator","version":"1.0.0"}\n'

def peer(path, direct, pipe):
    if direct:
        observed = path.stat()
        pipe.send({'observedSize': observed.st_size})
        assert pipe.recv() == 'creator-finished'
        with path.open('r+b') as file:
            current = os.fstat(file.fileno())
            assert (observed.st_dev, observed.st_ino) == (current.st_dev, current.st_ino)
            file.truncate(0)
            pipe.send({'repairTruncated': True})
            assert pipe.recv() == 'import-finished'
            file.write(PAYLOAD)
        assert path.read_bytes() == PAYLOAD
    else:
        assert not path.exists()
        pipe.send({'finalPathNotPublished': True})
        assert pipe.recv() == 'creator-finished'
        assert path.read_bytes() == PAYLOAD
        pipe.send({'matchingExistingFileNotRepaired': True})
        assert pipe.recv() == 'import-finished'
    pipe.send({'peerFinished': True})

def receive(pipe):
    assert pipe.poll(5), 'Owned model peer deadline exceeded'
    return pipe.recv()

def model(parent, direct):
    store = parent / ('direct-cas' if direct else 'atomic-cas')
    pending = parent / 'pending-cas'
    target = parent / ('direct-package.json' if direct else 'atomic-package.json')
    output = {'scenario': 'published-empty-then-in-place-repair' if direct else 'complete-private-file-before-publication'}
    file = (store if direct else pending).open('xb')
    pipe, child_pipe = multiprocessing.Pipe()
    child = multiprocessing.get_context('spawn').Process(target=peer, args=(store, direct, child_pipe))
    child.start()
    try:
        output['beforeWrite'] = receive(pipe)
        file.write(PAYLOAD)
        file.close()
        if not direct:
            os.link(pending, store)
            pending.unlink()
        pipe.send('creator-finished')
        output['beforeImport'] = receive(pipe)
        shutil.copyfile(store, target)
        output['importedBytes'] = target.stat().st_size
        try:
            json.loads(target.read_bytes())
            output['manifestParses'] = True
        except json.JSONDecodeError:
            output['manifestParses'] = False
        pipe.send('import-finished')
        output['peer'] = receive(pipe)
        child.join(5)
        assert not child.is_alive() and child.exitcode == 0
        output['storeValidAfterRepair'] = store.read_bytes() == PAYLOAD
        assert output['manifestParses'] is (not direct)
        assert output['storeValidAfterRepair']
        return output
    finally:
        file.close()
        if child.is_alive():
            child.terminate()
            child.join(5)
        pipe.close()
        child_pipe.close()

if __name__ == '__main__':
    scratch = pathlib.Path(__file__).parent / 'tmp'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cas-syscall-model-', dir=scratch) as root:
        print(json.dumps({'basis': 'Controlled two-process filesystem model only; no pnpm or Nix executed',
            'results': [model(pathlib.Path(root), True), model(pathlib.Path(root), False)]}), flush=True)
