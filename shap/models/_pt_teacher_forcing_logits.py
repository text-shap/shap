import numpy as np
import scipy as sp
from ..utils import record_import_error
from ._teacher_forcing_logits import TeacherForcingLogits

try:
    import torch
except ImportError as e:
    record_import_error("torch", "Torch could not be imported!", e)


class PTTeacherForcingLogits(TeacherForcingLogits):
    def __init__(
        self,
        model,
        tokenizer=None,
        generation_function_for_target_sentence_ids=None,
        similarity_model=None,
        similarity_tokenizer=None,
        device=None,
    ):
        """Generates scores (log odds) for output text explanation algorithms.

        This model inherits from TeacherForcingLogits. Check the superclass documentation for the generic methods the library implements for all its model.

        This class supports generation of log odds for PyTorch transformer models as well as functions. It also provides
        functionality to score custom output text by passing the generation_function_for_target_sentence_ids. In model agnostic
        cases (model is function) it expects a similarity_model and similarity_tokenizer to approximate log odd scores
        for target sentence generated by the model.

        Parameters
        ----------
        model: object or function
            A object of any pretrained transformer model or function which is to be explained.

        tokenizer: object
            A tokenizer object(PreTrainedTokenizer/PreTrainedTokenizerFast) which is used to tokenize source and target sentence.

        generation_function_for_target_sentence_ids: function
            A function which is used to generate custom target ids. Log odds will be generated for these custom target ids.

        similarity_model: object
            A pretrained transformer model object which is used in model agnostic scenario to approximate log odds.

        similarity_tokenizer: object
            A tokenizer object(PreTrainedTokenizer/PreTrainedTokenizerFast) which is used to tokenize sentence in model agnostic scenario.

        device: "cpu" or "cuda" or None
            By default, it infers if system has a gpu and accordingly sets device. Should be 'cpu' or 'gpu'.

        Returns
        -------
        numpy.array
            The scores (log odds) of generating target sentence ids using the model.
        """
        super(PTTeacherForcingLogits, self).__init__(
            model,
            tokenizer,
            generation_function_for_target_sentence_ids,
            similarity_model,
            similarity_tokenizer,
            device,
        )

        self.device = (
            torch.device("cuda" if torch.cuda.is_available() else "cpu")
            if device is None
            else device
        )

        if self.model_agnostic:
            self.similarity_model = similarity_model.to(self.device)
        else:
            self.model = model.to(self.device)

    def get_output_names_and_update_target_sentence_ids(self, X, X_opt=None):
        """Gets the output tokens from input(X) by computing the
            target sentence ids using the using the generation_function_for_target_sentence_ids()
            and next getting output names using the similarity_tokenizer.

        Parameters
        ----------
        X: string or numpy array
            Input(Text/Image) for an explanation row.
        X_opt: optional string or numpy.array
            Input(Text/Image) for an explanation row.
        Returns
        -------
        list
            A list of output tokens.
        """
        self.target_sentence_ids = (
            self.generation_function_for_target_sentence_ids(X, X_opt)
            .to(self.device)
            .to(torch.int64)
        )
        output_names = [
            self.similarity_tokenizer.decode([x]).strip()
            for x in self.target_sentence_ids[0, :].cpu().numpy()
        ]
        return output_names

    def get_source_sentence_ids(self, X, X_opt=None):
        """The function tokenizes source sentence.

        Parameters
        ----------
        X: string or tensor
            X could be a text or image.
        X_opt: optional string or numpy.array
            Input(Text/Image) for an explanation row.
        Returns
        -------
        tensor
            Tensor of source sentence ids.
        """
        # TODO: batch source_sentence_ids
        if self.model_agnostic:
            # In model agnostic case, we first pass the input through the model and then tokenize output sentence
            source_sentence = self.model(X)  # TODO: Does not handle X_opt
            source_sentence_ids = torch.tensor(
                [self.similarity_tokenizer.encode(source_sentence)]
            )
        else:
            # TODO: check if X is text/image cause presently only when X=text is supported to use model decoder
            source_sentence_ids = torch.tensor(
                [self.similarity_tokenizer.encode(X, X_opt)]
            )
        source_sentence_ids = source_sentence_ids.to(self.device).to(torch.int64)
        return source_sentence_ids

    def get_logodds(self, logits):
        """Calculates log odds from logits.

        This function passes the logits through softmax and then computes log odds for the target sentence ids.

        Parameters
        ----------
        logits: numpy.array
            An array of logits generated from the model.

        Returns
        -------
        numpy.array
            Computes log odds for corresponding target sentence ids.
        """
        logodds = []
        # pass logits through softmax, get the token corresponding score and convert back to log odds (as one vs all)
        for i in range(0, logits.shape[1] - 1):
            probs = (np.exp(logits[0][i]).T / np.exp(logits[0][i]).sum(-1)).T
            logit_dist = sp.special.logit(probs)
            logodds.append(logit_dist[self.target_sentence_ids[0, i].item()])
        return np.array(logodds)

    def get_teacher_forced_logits(self, source_sentence_ids, target_sentence_ids):
        """The function generates logits for transformer models.

        It generates logits for encoder-decoder models as well as decoder only models by using the teacher forcing technique.

        Parameters
        ----------
        source_sentence_ids: 2D tensor of shape (batch size, len of sequence)
            Tokenized ids fed to the model.

        target_sentence_ids: 2D tensor of shape (batch size, len of sequence)
            Tokenized ids for which logits are generated using the decoder.

        Returns
        -------
        numpy.array
            Decoder output logits for target sentence ids.
        """
        # set model to eval mode
        self.similarity_model.eval()
        # check if type of model architecture assigned in model config
        if (
            hasattr(self.similarity_model.config, "is_encoder_decoder")
            and not self.similarity_model.config.is_encoder_decoder
        ) and (
            hasattr(self.similarity_model.config, "is_decoder")
            and not self.similarity_model.config.is_decoder
        ):
            raise ValueError(
                "Please assign either of is_encoder_decoder or is_decoder to True in model config for extracting target sentence ids"
            )
        if self.similarity_model.config.is_encoder_decoder:
            # assigning decoder start token id as it is needed for encoder decoder model generation
            decoder_start_token_id = None
            if (
                hasattr(self.similarity_model.config, "decoder_start_token_id")
                and self.similarity_model.config.decoder_start_token_id is not None
            ):
                decoder_start_token_id = (
                    self.similarity_model.config.decoder_start_token_id
                )
            elif (
                hasattr(self.similarity_model.config, "bos_token_id")
                and self.similarity_model.config.bos_token_id is not None
            ):
                decoder_start_token_id = self.similarity_model.config.bos_token_id
            elif (
                hasattr(self.similarity_model.config, "decoder")
                and hasattr(self.similarity_model.config.decoder, "bos_token_id")
                and self.similarity_model.config.decoder.bos_token_id is not None
            ):
                decoder_start_token_id = (
                    self.similarity_model.config.decoder.bos_token_id
                )
            else:
                raise ValueError(
                    "No decoder_start_token_id or bos_token_id defined in config for encoder-decoder generation"
                )
            # concat decoder start token id to target sentence ids
            target_sentence_start_id = (
                torch.ones(
                    (source_sentence_ids.shape[0], 1),
                    dtype=source_sentence_ids.dtype,
                    device=source_sentence_ids.device,
                )
                * decoder_start_token_id
            )
            target_sentence_ids = torch.cat(
                (target_sentence_start_id, target_sentence_ids), dim=-1
            )
            # generate outputs and logits
            with torch.no_grad():
                outputs = self.similarity_model(
                    input_ids=source_sentence_ids,
                    decoder_input_ids=target_sentence_ids,
                    labels=target_sentence_ids,
                    return_dict=True,
                )
            logits = outputs.logits.detach().cpu().numpy().astype("float64")
        else:
            # check if source sentence ids are null then add bos token id to decoder
            if source_sentence_ids.shape[1] == 0:
                if (
                    hasattr(self.similarity_model.config, "bos_token_id")
                    and self.similarity_model.config.bos_token_id is not None
                ):
                    source_sentence_ids = (
                        torch.ones(
                            (source_sentence_ids.shape[0], 1),
                            dtype=source_sentence_ids.dtype,
                            device=source_sentence_ids.device,
                        )
                        * self.similarity_model.config.bos_token_id
                    )
                else:
                    raise ValueError(
                        "Context ids (source sentence ids) are null and no bos token defined in model config"
                    )
            # combine source and target sentence ids  to pass into decoder eg: in case of distillgpt2
            combined_sentence_ids = torch.cat(
                (source_sentence_ids, target_sentence_ids), dim=-1
            )
            # generate outputs and logits
            with torch.no_grad():
                outputs = self.similarity_model(
                    input_ids=combined_sentence_ids, return_dict=True
                )
            # extract only logits corresponding to target sentence ids
            logits = (
                outputs.logits.detach()
                .cpu()
                .numpy()[:, source_sentence_ids.shape[1] - 1 :, :]
                .astype("float64")
            )
        del outputs
        return logits